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
from tqdm import tqdm

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

# Get API keys from environment variables
SONARR_API_KEY = os.getenv('SONARR_API_KEY')
RADARR_API_KEY = os.getenv('RADARR_API_KEY')

def find_docker_compose():
    """Find docker-compose.yml in common locations."""
    possible_locations = [
        os.path.expanduser("~/docker/docker-compose.yml"),
        os.path.expanduser("~/docker-compose.yml"),
        os.path.expanduser("~/media-server/docker-compose.yml"),
        os.path.expanduser("~/docker/media/docker-compose.yml")
    ]
    
    for location in possible_locations:
        if os.path.exists(location):
            logger.info(f"Found docker-compose.yml at {location}")
            return location
    
    logger.warning("Could not find docker-compose.yml in common locations")
    return None

def get_media_folders_from_docker_compose(docker_compose_path=None, docker_env_path=None):
    """Extract media folders from docker-compose.yml file."""
    if not docker_compose_path:
        docker_compose_path = find_docker_compose()
    
    if not docker_compose_path:
        logger.error("Docker compose file not found. Please specify media directories manually.")
        return []
    
    if not docker_env_path and os.path.exists(os.path.dirname(docker_compose_path) + "/.env"):
        docker_env_path = os.path.dirname(docker_compose_path) + "/.env"
    
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
                # If yaml parsing fails, try a simple regex approach
                f.seek(0)
                content = f.read()
                return extract_media_folders_with_regex(content, env_vars)
    except Exception as e:
        logger.error(f"Error reading docker-compose file: {e}")
        return []
    
    media_folders = set()
    
    # Look for media folders in services
    for service_name, service_data in compose_data.get('services', {}).items():
        if service_name in ['jellyfin', 'sonarr', 'radarr', 'plex', 'emby', 'bazarr', 'jellyseerr', 'overseerr']:
            volumes = service_data.get('volumes', [])
            logger.info(f"Found {len(volumes)} volumes for service {service_name}")
            
            for volume in volumes:
                if isinstance(volume, str):
                    parts = volume.split(':')
                    if len(parts) >= 2:
                        host_path = parts[0]
                        
                        # Replace environment variables
                        for var_name, var_value in env_vars.items():
                            placeholder = f"${{{var_name}}}"
                            if placeholder in host_path:
                                host_path = host_path.replace(placeholder, var_value)
                        
                        # Check if the path looks like a media folder and exists
                        if os.path.exists(host_path) and os.path.isdir(host_path):
                            common_media_terms = ['media', 'movies', 'tv', 'television', 'films', 'videos', 'shows']
                            path_lower = host_path.lower()
                            
                            # Check if any media-related term is in the path
                            if any(term in path_lower for term in common_media_terms):
                                media_folders.add(host_path)
                                logger.info(f"Added media folder: {host_path}")
    
    return list(media_folders)

def extract_media_folders_with_regex(content, env_vars):
    """Extract media folders using regex when YAML parsing fails."""
    media_folders = set()
    
    # Find volume mappings in the content
    volume_pattern = r'- ([^:]+):.*'
    for service in ['jellyfin', 'sonarr', 'radarr', 'plex', 'emby', 'bazarr', 'jellyseerr', 'overseerr']:
        service_section = re.search(rf'{service}:.*?volumes:(.*?)(?:labels:|ports:|environment:|$)', 
                                   content, re.DOTALL)
        if service_section:
            volumes_text = service_section.group(1)
            for line in volumes_text.split('\n'):
                match = re.search(volume_pattern, line.strip())
                if match:
                    host_path = match.group(1)
                    
                    # Replace environment variables
                    for var_name, var_value in env_vars.items():
                        placeholder = f"${{{var_name}}}"
                        if placeholder in host_path:
                            host_path = host_path.replace(placeholder, var_value)
                    
                    # Check if the path exists and looks like a media folder
                    if os.path.exists(host_path) and os.path.isdir(host_path):
                        common_media_terms = ['media', 'movies', 'tv', 'television', 'films', 'videos', 'shows']
                        path_lower = host_path.lower()
                        
                        # Check if any media-related term is in the path
                        if any(term in path_lower for term in common_media_terms):
                            media_folders.add(host_path)
                            logger.info(f"Added media folder (regex): {host_path}")
    
    return list(media_folders)

class MediaManager:
    def __init__(self, config_file=None):
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
        
        # Store official paths from Sonarr and Radarr
        self.sonarr_series_paths = {}  # series title -> path
        self.radarr_movie_paths = {}   # movie title -> path
        
        # Directory mapping
        self.media_folder_map = {}  # folder name -> list of full paths
        
        # Store duplicates
        self.duplicates = []
    
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
            logger.error(f"Error loading config file: {e}")
            return {}
    
    def get_sonarr_series(self):
        """Get all series from Sonarr."""
        logger.info("Fetching series from Sonarr...")
        try:
            response = requests.get(
                f"{self.sonarr_url}/api/v3/series",
                headers={"X-Api-Key": self.sonarr_api_key},
                timeout=10
            )
            
            if response.status_code != 200:
                logger.warning(f"Error fetching series from Sonarr: Status code {response.status_code}")
                logger.warning(f"Response: {response.text}")
                return []
            
            series_list = response.json()
            
            for series in series_list:
                title = series.get('title')
                path = series.get('path')
                if title and path:
                    self.sonarr_series_paths[title] = path
                    
            logger.info(f"Found {len(self.sonarr_series_paths)} series in Sonarr")
            return series_list
        except requests.exceptions.ConnectionError:
            logger.error(f"Connection error: Could not connect to Sonarr at {self.sonarr_url}")
            logger.error("Please check that Sonarr is running and the URL is correct in config.json")
            return []
        except Exception as e:
            logger.error(f"Error fetching series from Sonarr: {e}")
            return []
    
    def get_radarr_movies(self):
        """Get all movies from Radarr."""
        logger.info("Fetching movies from Radarr...")
        try:
            response = requests.get(
                f"{self.radarr_url}/api/v3/movie",
                headers={"X-Api-Key": self.radarr_api_key},
                timeout=10
            )
            
            if response.status_code != 200:
                logger.warning(f"Error fetching movies from Radarr: Status code {response.status_code}")
                logger.warning(f"Response: {response.text}")
                return []
            
            movie_list = response.json()
            
            for movie in movie_list:
                title = movie.get('title')
                path = movie.get('path')
                if title and path:
                    self.radarr_movie_paths[title] = path
                    
            logger.info(f"Found {len(self.radarr_movie_paths)} movies in Radarr")
            return movie_list
        except requests.exceptions.ConnectionError:
            logger.error(f"Connection error: Could not connect to Radarr at {self.radarr_url}")
            logger.error("Please check that Radarr is running and the URL is correct in config.json")
            return []
        except Exception as e:
            logger.error(f"Error fetching movies from Radarr: {e}")
            return []
    
    def scan_directories(self, directories):
        """Quickly scan directories for duplicate media folders."""
        logger.info("Scanning for duplicate media folders...")
        
        # Map of folder names to their full paths
        folder_map = defaultdict(list)
        
        # Progress bar for directory scanning
        directories_with_size = [(directory, sum(1 for _ in os.listdir(directory))) 
                                 for directory in directories if os.path.exists(directory)]
        total_size = sum(size for _, size in directories_with_size)
        
        # Scan top-level directories in each specified media folder
        with tqdm(total=total_size, desc="Scanning directories") as pbar:
            for directory, _ in directories_with_size:
                root_path = Path(directory).resolve()
                if not root_path.exists():
                    logger.warning(f"Directory {directory} does not exist, skipping...")
                    continue
                    
                logger.info(f"Scanning {root_path}...")
                
                try:
                    # Only scan immediate subdirectories - these should be show/movie folders
                    for item in os.listdir(root_path):
                        pbar.update(1)
                        item_path = os.path.join(root_path, item)
                        if os.path.isdir(item_path):
                            # Store the directory in our map
                            folder_map[item].append(item_path)
                except Exception as e:
                    logger.error(f"Error scanning {root_path}: {e}")
        
        # Find folders that appear in multiple directories
        duplicate_folders = {name: paths for name, paths in folder_map.items() if len(paths) > 1}
        
        logger.info(f"Found {len(duplicate_folders)} potential duplicate folders")
        self.media_folder_map = folder_map
        
        return duplicate_folders
    
    def determine_official_paths(self, duplicate_folders):
        """Determine which path is the official one according to Sonarr/Radarr."""
        logger.info("Determining official paths for duplicates...")
        
        for folder_name, folder_paths in duplicate_folders.items():
            # Try to find a match in Sonarr or Radarr
            official_path = None
            server_name = None
            
            # Check if any of the paths match a Sonarr series path
            for title, path in self.sonarr_series_paths.items():
                for folder_path in folder_paths:
                    if folder_path in path or path in folder_path:
                        official_path = path
                        server_name = "Sonarr"
                        break
                if official_path:
                    break
            
            # If not found in Sonarr, check Radarr
            if not official_path:
                for title, path in self.radarr_movie_paths.items():
                    for folder_path in folder_paths:
                        if folder_path in path or path in folder_path:
                            official_path = path
                            server_name = "Radarr"
                            break
                    if official_path:
                        break
            
            # Create duplicate entry
            duplicate_info = {
                'folder_name': folder_name,
                'official_path': official_path,
                'server': server_name,
                'all_paths': folder_paths,
                'duplicate_paths': []
            }
            
            # Determine duplicate paths (all paths except the official one)
            if official_path:
                for path in folder_paths:
                    if not (path in official_path or official_path in path):
                        duplicate_info['duplicate_paths'].append(path)
            else:
                # If no official path found, just list all paths as duplicates
                duplicate_info['duplicate_paths'] = folder_paths
            
            if duplicate_info['duplicate_paths']:
                self.duplicates.append(duplicate_info)
        
        logger.info(f"Found {len(self.duplicates)} duplicates with known official paths")
        return self.duplicates
    
    def generate_report(self, output_file='duplicate_folders_report.txt'):
        """Generate a report of duplicate media folders."""
        logger.info(f"Generating report to {output_file}...")
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("=== Media Duplicate Folders Report ===\n\n")
            
            if not self.duplicates:
                f.write("No duplicates found.\n")
                return
            
            for dup in self.duplicates:
                f.write(f"Folder: {dup['folder_name']}\n")
                
                if dup['official_path']:
                    f.write(f"Official Path ({dup['server']}): {dup['official_path']}\n")
                else:
                    f.write("Official Path: Unknown (not found in Sonarr or Radarr)\n")
                
                f.write("\nDuplicate Paths:\n")
                for path in dup['duplicate_paths']:
                    f.write(f"  {path}\n")
                
                f.write("\n" + "="*50 + "\n\n")
            
            # Add summary
            f.write(f"\nSummary:\n")
            f.write(f"Total duplicate folders: {len(self.duplicates)}\n")
            
            # Count total number of duplicate paths
            total_duplicate_paths = sum(len(dup['duplicate_paths']) for dup in self.duplicates)
            f.write(f"Total duplicate paths: {total_duplicate_paths}\n")
        
        logger.info(f"Report written to {output_file}")

def main():
    parser = argparse.ArgumentParser(description='Quickly find duplicate media folders')
    parser.add_argument('directories', nargs='*', help='Directories to scan (optional if using --auto-detect)')
    parser.add_argument('--config', type=str, default='config.json',
                        help='Path to configuration file (default: ./config.json)')
    parser.add_argument('--output', type=str, default='duplicate_folders_report.txt',
                        help='Output file path for the duplicate report (default: ./duplicate_folders_report.txt)')
    parser.add_argument('--auto-detect', action='store_true',
                        help='Automatically detect media folders from docker-compose.yml')
    parser.add_argument('--docker-compose', type=str, default=None,
                        help='Path to docker-compose.yml file (optional, will try common locations if not specified)')
    parser.add_argument('--docker-env', type=str, default=None,
                        help='Path to docker .env file (optional, will look next to docker-compose.yml if not specified)')
    
    args = parser.parse_args()
    
    # Get directories to scan
    directories = args.directories
    
    # Auto-detect media folders from docker-compose if requested
    if args.auto_detect:
        docker_media_folders = get_media_folders_from_docker_compose(args.docker_compose, args.docker_env)
        if docker_media_folders:
            logger.info(f"Auto-detected {len(docker_media_folders)} media folders from docker-compose.yml")
            for folder in docker_media_folders:
                logger.info(f"  {folder}")
            
            # Add auto-detected folders to the list
            directories.extend(docker_media_folders)
    
    if not directories:
        logger.error("No directories specified and auto-detection found no folders!")
        parser.print_help()
        sys.exit(1)
    
    # Create media manager
    manager = MediaManager(args.config)
    
    # Get data from Sonarr and Radarr
    manager.get_sonarr_series()
    manager.get_radarr_movies()
    
    # Scan directories for duplicate folders
    duplicate_folders = manager.scan_directories(directories)
    
    # Determine official paths
    if duplicate_folders:
        manager.determine_official_paths(duplicate_folders)
    
    # Generate report
    manager.generate_report(args.output)
    
    # Output summary
    if manager.duplicates:
        print(f"\nFound {len(manager.duplicates)} duplicate folders.")
        print(f"See {args.output} for details.")
    else:
        print("\nNo duplicate folders found.")

if __name__ == '__main__':
    main() 