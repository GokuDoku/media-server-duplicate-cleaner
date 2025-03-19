#!/usr/bin/env python3

import os
import sys
import json
import argparse
import logging
import requests
import re
import yaml
from pathlib import Path
from typing import Dict, List, Set, Tuple
from collections import defaultdict
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

# Load user-defined root folders that should not be considered duplicates
CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
PROTECTED_DIRS_FILE = os.path.join(CONFIG_DIR, "protected_dirs.json")

# Default list of folders that should be protected from being considered duplicates
DEFAULT_ROOT_FOLDERS_TO_IGNORE = [
    # These are just examples - users should configure their own in protected_dirs.json
    "/media/Movies",
    "/media/TV",
    "/media/Television"
]

# Get API keys from environment variables
SONARR_API_KEY = os.getenv('SONARR_API_KEY')
RADARR_API_KEY = os.getenv('RADARR_API_KEY')

def get_docker_mappings(docker_compose_path=None, docker_env_path=None):
    """Extract volume mappings from docker-compose.yml file."""
    # Allow overriding paths via parameters
    if not docker_compose_path:
        docker_compose_path = os.path.expanduser("~/docker/docker-compose.yml")
        # Try alternative common locations if not found
        if not os.path.exists(docker_compose_path):
            alternatives = [
                os.path.expanduser("~/docker-compose.yml"),
                os.path.expanduser("~/docker/media/docker-compose.yml"),
                os.path.expanduser("~/media-server/docker-compose.yml")
            ]
            for alt_path in alternatives:
                if os.path.exists(alt_path):
                    docker_compose_path = alt_path
                    break
    
    if not docker_env_path and os.path.exists(os.path.dirname(docker_compose_path) + "/.env"):
        docker_env_path = os.path.dirname(docker_compose_path) + "/.env"
    
    mappings = []
    
    if not os.path.exists(docker_compose_path):
        logger.warning(f"Docker compose file not found at {docker_compose_path}")
        logger.info("You may need to manually configure paths in config.json")
        return mappings
    
    logger.info(f"Found docker-compose.yml at {docker_compose_path}")
    
    # Load environment variables from docker-compose .env file
    env_vars = {}
    if docker_env_path and os.path.exists(docker_env_path):
        logger.info(f"Loading environment variables from {docker_env_path}")
        with open(docker_env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key] = value
    
    # Load docker-compose file
    try:
        with open(docker_compose_path, 'r') as f:
            try:
                compose_data = yaml.safe_load(f)
            except yaml.YAMLError:
                logger.warning(f"Error parsing docker-compose file as YAML")
                return mappings
    except Exception as e:
        logger.warning(f"Error reading docker-compose file: {e}")
        return mappings
    
    # Extract volume mappings from Sonarr and Radarr
    for service_name in ['sonarr', 'radarr']:
        if service_name in compose_data.get('services', {}):
            service_data = compose_data['services'][service_name]
            volumes = service_data.get('volumes', [])
            
            for volume in volumes:
                if isinstance(volume, str):
                    parts = volume.split(':')
                    if len(parts) >= 2:
                        host_path = parts[0]
                        container_path = parts[1]
                        
                        # Replace environment variables
                        for var_name, var_value in env_vars.items():
                            placeholder = f"${{{var_name}}}"
                            if placeholder in host_path:
                                host_path = host_path.replace(placeholder, var_value)
                        
                        # Add all mappings, filtering can be done later if needed
                        mappings.append({
                            'service': service_name,
                            'host_path': host_path,
                            'container_path': container_path
                        })
            
            logger.info(f"Found {len(volumes)} volume mappings for {service_name}")
    
    return mappings

def convert_container_path_to_host_path(container_path, mappings):
    """Convert a container path to a host path using the volume mappings."""
    # Sort mappings by container path length in descending order to match the most specific first
    sorted_mappings = sorted(mappings, key=lambda m: len(m['container_path']), reverse=True)
    
    for mapping in sorted_mappings:
        if container_path.startswith(mapping['container_path']):
            # Replace the container path with the host path
            relative_path = container_path[len(mapping['container_path']):].lstrip('/')
            host_path = os.path.join(mapping['host_path'], relative_path)
            return host_path
    
    return container_path  # Return original if no mapping found

class MediaServerPathLookup:
    def __init__(self, config_file=None, docker_compose=None, docker_env=None):
        """Initialize with configuration."""
        self.config = self._load_config(config_file)
        self.sonarr_url = self.config.get('sonarr', {}).get('url', 'http://localhost:8989')
        self.radarr_url = self.config.get('radarr', {}).get('url', 'http://localhost:7878')
        
        # Use API keys from environment variables or config file
        self.sonarr_api_key = SONARR_API_KEY or self.config.get('sonarr', {}).get('api_key')
        self.radarr_api_key = RADARR_API_KEY or self.config.get('radarr', {}).get('api_key')
        
        if not self.sonarr_api_key or not self.radarr_api_key:
            logger.warning("API keys for Sonarr and/or Radarr are missing!")
            logger.warning("Will continue with limited functionality")
        
        logger.info(f"Using Sonarr URL: {self.sonarr_url}")
        logger.info(f"Using Radarr URL: {self.radarr_url}")
        
        # Get docker mappings
        self.docker_mappings = get_docker_mappings(docker_compose, docker_env)
        
        # Store official paths with detailed information
        self.sonarr_series_details = {}  # series title -> details
        self.radarr_movie_details = {}   # movie title -> details
        
        # Map folder names to paths
        self.folder_to_path_map = {}  # folder name -> {type, title, path}
        
        # Load protected directories list
        self.root_folders_to_ignore = load_protected_dirs()
        logger.info(f"Protected directories: {self.root_folders_to_ignore}")
    
    def _load_config(self, config_file):
        """Load configuration from file."""
        if not config_file:
            config_file = 'config.json'
            
        if not os.path.exists(config_file):
            logger.warning(f"Config file {config_file} not found, using defaults")
            return {
                'sonarr': {'url': 'http://localhost:8989', 'api_key': None},
                'radarr': {'url': 'http://localhost:7878', 'api_key': None}
            }
            
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
                logger.info(f"Successfully loaded config from {config_file}")
                return config
        except Exception as e:
            logger.warning(f"Error loading config file: {e}")
            return {}
    
    def get_sonarr_series(self):
        """Get all series from Sonarr with detailed information."""
        logger.info("Fetching series from Sonarr...")
        
        if not self.sonarr_api_key:
            logger.warning("Sonarr API key is missing, skipping series lookup")
            return {}
        
        try:
            api_url = f"{self.sonarr_url}/api/v3/series"
            logger.info(f"Requesting: {api_url}")
            
            response = requests.get(
                api_url,
                headers={"X-Api-Key": self.sonarr_api_key},
                timeout=10
            )
            
            if response.status_code != 200:
                logger.warning(f"Error fetching series from Sonarr: Status code {response.status_code}")
                logger.warning(f"Response: {response.text}")
                return {}
            
            series_list = response.json()
            
            for series in series_list:
                title = series.get('title')
                path = series.get('path')
                
                if title and path:
                    # Convert container path to host path if necessary
                    host_path = convert_container_path_to_host_path(path, self.docker_mappings)
                    
                    # Store detailed info
                    self.sonarr_series_details[title] = {
                        'title': title,
                        'container_path': path,
                        'host_path': host_path,
                        'id': series.get('id'),
                        'monitored': series.get('monitored', False),
                        'tvdb_id': series.get('tvdbId'),
                        'status': series.get('status')
                    }
                    
                    # Get folder name from path
                    folder_name = os.path.basename(host_path)
                    self.folder_to_path_map[folder_name] = {
                        'type': 'series',
                        'title': title,
                        'host_path': host_path
                    }
            
            logger.info(f"Found {len(self.sonarr_series_details)} series in Sonarr")
            
            # Print a sample of paths for debugging
            if self.sonarr_series_details:
                sample = list(self.sonarr_series_details.items())[:3]
                for title, details in sample:
                    logger.info(f"Sample series: {title}")
                    logger.info(f"  Container path: {details['container_path']}")
                    logger.info(f"  Host path: {details['host_path']}")
            
            return self.sonarr_series_details
        except requests.RequestException as e:
            logger.warning(f"Error connecting to Sonarr: {e}")
            return {}
        except Exception as e:
            logger.warning(f"Unexpected error fetching series from Sonarr: {e}")
            return {}
    
    def get_radarr_movies(self):
        """Get all movies from Radarr with detailed information."""
        logger.info("Fetching movies from Radarr...")
        
        if not self.radarr_api_key:
            logger.warning("Radarr API key is missing, skipping movie lookup")
            return {}
        
        try:
            api_url = f"{self.radarr_url}/api/v3/movie"
            logger.info(f"Requesting: {api_url}")
            
            response = requests.get(
                api_url,
                headers={"X-Api-Key": self.radarr_api_key},
                timeout=10
            )
            
            if response.status_code != 200:
                logger.warning(f"Error fetching movies from Radarr: Status code {response.status_code}")
                logger.warning(f"Response: {response.text}")
                return {}
            
            movie_list = response.json()
            
            for movie in movie_list:
                title = movie.get('title')
                path = movie.get('path')
                
                if title and path:
                    # Convert container path to host path if necessary
                    host_path = convert_container_path_to_host_path(path, self.docker_mappings)
                    
                    # Store detailed info
                    self.radarr_movie_details[title] = {
                        'title': title,
                        'container_path': path,
                        'host_path': host_path,
                        'id': movie.get('id'),
                        'monitored': movie.get('monitored', False),
                        'tmdb_id': movie.get('tmdbId'),
                        'year': movie.get('year')
                    }
                    
                    # Get folder name from path
                    folder_name = os.path.basename(host_path)
                    self.folder_to_path_map[folder_name] = {
                        'type': 'movie',
                        'title': title,
                        'host_path': host_path
                    }
            
            logger.info(f"Found {len(self.radarr_movie_details)} movies in Radarr")
            
            # Print a sample of paths for debugging
            if self.radarr_movie_details:
                sample = list(self.radarr_movie_details.items())[:3]
                for title, details in sample:
                    logger.info(f"Sample movie: {title}")
                    logger.info(f"  Container path: {details['container_path']}")
                    logger.info(f"  Host path: {details['host_path']}")
            
            return self.radarr_movie_details
        except requests.RequestException as e:
            logger.warning(f"Error connecting to Radarr: {e}")
            return {}
        except Exception as e:
            logger.warning(f"Unexpected error fetching movies from Radarr: {e}")
            return {}

    def add_custom_mappings(self, mappings_file=None):
        """Add custom mappings to handle known paths."""
        if mappings_file and os.path.exists(mappings_file):
            try:
                logger.info(f"Loading custom mappings from {mappings_file}")
                with open(mappings_file, 'r') as f:
                    custom_mappings = json.load(f)
                    
                for folder_name, mapping in custom_mappings.items():
                    self.folder_to_path_map[folder_name] = mapping
                    logger.info(f"Added custom mapping for {folder_name}")
            except Exception as e:
                logger.warning(f"Error loading custom mappings file: {e}")
    
    def is_protected_path(self, path):
        """Check if a path is a protected path that should not be considered a duplicate."""
        for root_folder in self.root_folders_to_ignore:
            if path == root_folder or path.startswith(root_folder + '/'):
                # Specifically flag main media directories as protected
                basename = os.path.basename(path)
                if basename.lower() in ['films', 'movies', 'television', 'tv', 'videos']:
                    logger.warning(f"Protected main directory detected: {path}")
                    return True
        return False
    
    def are_related_media_paths(self, path1, path2, folder_name):
        """Check if two paths appear to be related media paths for the same content."""
        # Extract basenames
        basename1 = os.path.basename(path1)
        basename2 = os.path.basename(path2)
        
        # Check for exact basename match
        if basename1.lower() == basename2.lower():
            return True
        
        # Check for folder name match with basename
        if folder_name and (basename1.lower() == folder_name.lower() or basename2.lower() == folder_name.lower()):
            return True
        
        # Check for common media path patterns
        # Movie with year pattern: "Movie Name (Year)"
        movie_pattern = r'(.+?)(?:\s+\((\d{4})\))?$'
        match1 = re.match(movie_pattern, basename1)
        match2 = re.match(movie_pattern, basename2)
        
        if match1 and match2:
            movie_name1 = match1.group(1).lower().strip()
            movie_name2 = match2.group(1).lower().strip()
            
            # If movie names match
            if movie_name1 == movie_name2:
                return True
            
            # If one name is contained in the other (accounting for "Extended", "Director's Cut", etc.)
            if movie_name1 in movie_name2 or movie_name2 in movie_name1:
                return True
        
        # Check for common parent directories for media
        media_dirs = ['Movies', 'Television', 'TV', 'Series', 'Films', 'shows']
        parent1 = os.path.basename(os.path.dirname(path1)).lower()
        parent2 = os.path.basename(os.path.dirname(path2)).lower()
        
        # If both are in media directories and have the same basename
        if (parent1 in map(str.lower, media_dirs) and parent2 in map(str.lower, media_dirs) and 
            basename1.lower() == basename2.lower()):
            return True
        
        return False
    
    def lookup_duplicate_folders(self, duplicate_folders_file):
        """Look up official paths for duplicate folders."""
        if not os.path.exists(duplicate_folders_file):
            logger.error(f"Duplicate folders file not found: {duplicate_folders_file}")
            return []
        
        logger.info(f"Processing duplicate folders from {duplicate_folders_file}")
        
        results = []
        current_folder = None
        duplicate_paths = []
        
        # Parse the duplicate folders report
        with open(duplicate_folders_file, 'r') as f:
            content = f.read()
            logger.info(f"File size: {len(content)} bytes")
            
            # Split the content into records separated by the delimiter line
            records = content.split("==================================================")
            logger.info(f"Found {len(records)} records in the file")
            
            for record in records:
                if not record.strip():
                    continue
                
                folder_match = re.search(r"Folder: (.*?)$", record, re.MULTILINE)
                if not folder_match:
                    continue
                
                current_folder = folder_match.group(1).strip()
                logger.info(f"Processing folder: {current_folder}")
                
                duplicate_paths = []
                path_matches = re.finditer(r"^\s+(/.*?)$", record, re.MULTILINE)
                for match in path_matches:
                    path = match.group(1).strip()
                    # Skip protected paths
                    if not self.is_protected_path(path):
                        duplicate_paths.append(path)
                    else:
                        logger.warning(f"Skipping protected path: {path}")
                
                if duplicate_paths:
                    logger.info(f"Found {len(duplicate_paths)} duplicate paths")
                    official_info = self.get_official_path_for_folder(current_folder, duplicate_paths)
                    
                    if official_info:
                        logger.info(f"Found official path: {official_info.get('host_path')}")
                    else:
                        logger.info(f"No official path found for {current_folder}")
                    
                    results.append({
                        'folder': current_folder,
                        'duplicate_paths': duplicate_paths,
                        'official_info': official_info
                    })
        
        logger.info(f"Processed {len(results)} folders with duplicates")
        return results
    
    def get_official_path_for_folder(self, folder_name, duplicate_paths):
        """Get the official path for a folder based on Sonarr/Radarr data."""
        # First check direct match by folder name
        if folder_name in self.folder_to_path_map:
            logger.info(f"Found direct folder name match for {folder_name}")
            return self.folder_to_path_map[folder_name]
        
        # Try to find a match by comparing duplicate paths with known paths
        for title, details in self.sonarr_series_details.items():
            host_path = details['host_path']
            host_basename = os.path.basename(host_path)
            
            for dup_path in duplicate_paths:
                dup_basename = os.path.basename(dup_path)
                
                # Only consider paths with the same basename or where one contains the other
                if (host_basename.lower() == dup_basename.lower() or 
                    host_basename.lower() in dup_basename.lower() or 
                    dup_basename.lower() in host_basename.lower()):
                    
                    # Stricter path matching - require shared parent directory or exact match
                    if (host_path == dup_path or 
                        os.path.dirname(host_path) == os.path.dirname(dup_path) or
                        # One path is a subdirectory of the other
                        host_path.startswith(dup_path + '/') or
                        dup_path.startswith(host_path + '/') or
                        # Use the enhanced media path relation check
                        self.are_related_media_paths(host_path, dup_path, folder_name)):
                        
                        logger.info(f"Found strict path match for {folder_name} with series {title}")
                        logger.info(f"  Official: {host_path}")
                        logger.info(f"  Duplicate: {dup_path}")
                        return {
                            'type': 'series',
                            'title': title,
                            'host_path': host_path,
                            'match_type': 'path_comparison'
                        }
        
        for title, details in self.radarr_movie_details.items():
            host_path = details['host_path']
            host_basename = os.path.basename(host_path)
            
            for dup_path in duplicate_paths:
                dup_basename = os.path.basename(dup_path)
                
                # Only consider paths with the same basename or where one contains the other
                if (host_basename.lower() == dup_basename.lower() or 
                    host_basename.lower() in dup_basename.lower() or 
                    dup_basename.lower() in host_basename.lower()):
                    
                    # Stricter path matching
                    if (host_path == dup_path or 
                        os.path.dirname(host_path) == os.path.dirname(dup_path) or
                        # One path is a subdirectory of the other
                        host_path.startswith(dup_path + '/') or
                        dup_path.startswith(host_path + '/') or
                        # Use the enhanced media path relation check
                        self.are_related_media_paths(host_path, dup_path, folder_name)):
                        
                        logger.info(f"Found strict path match for {folder_name} with movie {title}")
                        logger.info(f"  Official: {host_path}")
                        logger.info(f"  Duplicate: {dup_path}")
                        return {
                            'type': 'movie',
                            'title': title,
                            'host_path': host_path,
                            'match_type': 'path_comparison'
                        }
        
        # If still not found, try fuzzy matching on folder names
        if folder_name:
            # Look for similar folder names in series
            for title, details in self.sonarr_series_details.items():
                db_folder = os.path.basename(details['host_path'])
                if folder_name.lower() == db_folder.lower() or folder_name.lower() in db_folder.lower() or db_folder.lower() in folder_name.lower():
                    logger.info(f"Found fuzzy name match for {folder_name} with series {title}")
                    return {
                        'type': 'series',
                        'title': title,
                        'host_path': details['host_path'],
                        'match_type': 'fuzzy_name'
                    }
            
            # Look for similar folder names in movies
            for title, details in self.radarr_movie_details.items():
                db_folder = os.path.basename(details['host_path'])
                if folder_name.lower() == db_folder.lower() or folder_name.lower() in db_folder.lower() or db_folder.lower() in folder_name.lower():
                    logger.info(f"Found fuzzy name match for {folder_name} with movie {title}")
                    return {
                        'type': 'movie',
                        'title': title,
                        'host_path': details['host_path'],
                        'match_type': 'fuzzy_name'
                    }
        
        # No match found
        return None
    
    def generate_updated_report(self, results, output_file='updated_duplicate_report.txt'):
        """Generate an updated report with official paths."""
        logger.info(f"Generating updated report to {output_file}")
        
        with open(output_file, 'w') as f:
            f.write("=== Updated Media Duplicates Report ===\n\n")
            
            for result in results:
                folder = result['folder']
                official_info = result['official_info']
                duplicate_paths = result['duplicate_paths']
                
                f.write(f"Folder: {folder}\n")
                
                if official_info:
                    f.write(f"Official Path ({official_info['type']}): {official_info['host_path']}\n")
                    f.write(f"Title: {official_info['title']}\n")
                    f.write(f"Match Type: {official_info.get('match_type', 'direct')}\n")
                else:
                    f.write("Official Path: Unknown (not found in Sonarr or Radarr)\n")
                
                f.write("\nDuplicate Paths:\n")
                for path in duplicate_paths:
                    is_official = official_info and (path == official_info['host_path'] or official_info['host_path'] in path)
                    if is_official:
                        f.write(f"  {path} (OFFICIAL)\n")
                    else:
                        f.write(f"  {path}\n")
                
                f.write("\n" + "="*50 + "\n\n")
            
            # Add summary
            f.write("\nSummary:\n")
            found_count = sum(1 for r in results if r['official_info'])
            not_found_count = sum(1 for r in results if not r['official_info'])
            
            f.write(f"Total duplicate folders: {len(results)}\n")
            f.write(f"Found in Sonarr/Radarr: {found_count}\n")
            f.write(f"Not found in Sonarr/Radarr: {not_found_count}\n")
        
        logger.info(f"Updated report written to {output_file}")
        return output_file

def main():
    parser = argparse.ArgumentParser(description='Look up official paths for duplicate folders in Sonarr and Radarr')
    parser.add_argument('--config', type=str, default='config.json',
                      help='Path to configuration file (default: ./config.json)')
    parser.add_argument('--input', type=str, default='duplicate_folders_report.txt',
                      help='Input file with duplicate folders (default: ./duplicate_folders_report.txt)')
    parser.add_argument('--output', type=str, default='updated_duplicate_report_fixed.txt',
                      help='Output file for updated report (default: ./updated_duplicate_report_fixed.txt)')
    parser.add_argument('--mappings', type=str, default=None,
                      help='Custom mappings file in JSON format (optional)')
    parser.add_argument('--docker-compose', type=str, default=None,
                      help='Path to docker-compose.yml file (optional, will try common locations if not specified)')
    parser.add_argument('--docker-env', type=str, default=None,
                      help='Path to docker .env file (optional, will look next to docker-compose.yml if not specified)')
    
    args = parser.parse_args()
    
    # Create path lookup
    lookup = MediaServerPathLookup(args.config, args.docker_compose, args.docker_env)
    
    # Get data from Sonarr and Radarr
    lookup.get_sonarr_series()
    lookup.get_radarr_movies()
    
    # Add custom mappings if provided
    lookup.add_custom_mappings(args.mappings)
    
    # Look up duplicate folders
    results = lookup.lookup_duplicate_folders(args.input)
    
    # Generate updated report
    if results:
        output_file = lookup.generate_updated_report(results, args.output)
        print(f"Generated updated report: {output_file}")
        print(f"Found official paths for {sum(1 for r in results if r['official_info'])} out of {len(results)} duplicate folders")
    else:
        print("No duplicate folders found or could not parse input file")

if __name__ == '__main__':
    main() 