#!/usr/bin/env python3

import os
import sys
from pathlib import Path
import json
import argparse
import logging
import requests
from typing import Dict, List
import shutil
from urllib.parse import urljoin
import concurrent.futures

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class MediaServer:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.headers = {
            'X-Api-Key': api_key,
            'Content-Type': 'application/json'
        }
        self._paths_cache = None

class Sonarr(MediaServer):
    def get_series_paths(self) -> Dict[str, Dict]:
        """Get all series paths from Sonarr with caching."""
        if self._paths_cache is None:
            try:
                response = requests.get(
                    f"{self.base_url}/api/v3/series",
                    headers=self.headers,
                    timeout=10
                )
                response.raise_for_status()
                series = response.json()
                self._paths_cache = {
                    self._normalize_path(s['path']): {
                        'title': s['title'],
                        'path': s['path'],
                        'quality': s.get('qualityProfileId'),
                        'size': s.get('sizeOnDisk', 0)
                    }
                    for s in series
                }
            except Exception as e:
                logger.error(f"Error fetching Sonarr series: {e}")
                self._paths_cache = {}
        return self._paths_cache

class Radarr(MediaServer):
    def get_movie_paths(self) -> Dict[str, Dict]:
        """Get all movie paths from Radarr with caching."""
        if self._paths_cache is None:
            try:
                response = requests.get(
                    f"{self.base_url}/api/v3/movie",
                    headers=self.headers,
                    timeout=10
                )
                response.raise_for_status()
                movies = response.json()
                self._paths_cache = {
                    self._normalize_path(m['path']): {
                        'title': m['title'],
                        'path': m['path'],
                        'quality': m.get('qualityProfileId'),
                        'size': m.get('sizeOnDisk', 0)
                    }
                    for m in movies
                }
            except Exception as e:
                logger.error(f"Error fetching Radarr movies: {e}")
                self._paths_cache = {}
        return self._paths_cache

def _normalize_path(path: str) -> str:
    """Normalize path for comparison."""
    return str(Path(path).resolve())

def load_config() -> Dict:
    """Load configuration from config file."""
    config_path = Path.home() / '.config' / 'media_cleanup' / 'config.json'
    
    if not config_path.exists():
        # Create default config
        config = {
            'sonarr': {
                'url': 'http://localhost:8989',
                'api_key': 'your_sonarr_api_key'
            },
            'radarr': {
                'url': 'http://localhost:7878',
                'api_key': 'your_radarr_api_key'
            }
        }
        
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=4)
            
        logger.error(f"Please configure your API keys in {config_path}")
        sys.exit(1)
        
    with open(config_path) as f:
        return json.load(f)

def parse_report(report_file: str) -> List[Dict]:
    """Parse the duplicate report and return structured data."""
    groups = []
    current_group = None
    
    with open(report_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    for line in lines:
        line = line.strip()
        if line.startswith('===') and len(line) > 10:  # New group
            if current_group:
                groups.append(current_group)
            current_group = {
                'base_folder': None,
                'base_files': [],
                'similar_folders': [],
                'similar_files': []
            }
        elif line.startswith('Base Folder:'):
            if current_group:
                current_group['base_folder'] = line.replace('Base Folder:', '').strip()
        elif line.startswith('Similar Folder'):
            # Start collecting files for a new similar folder
            current_group['similar_folders'].append(line)
        elif line.startswith('  /'):  # File path
            # Extract path and size
            parts = line.strip().split(' (')
            path = parts[0].strip()
            size = parts[1].replace(')', '') if len(parts) > 1 else 'Unknown'
            
            if current_group:
                if current_group['similar_folders']:
                    current_group['similar_files'].append((path, size))
                else:
                    current_group['base_files'].append((path, size))
    
    if current_group:
        groups.append(current_group)
    
    return groups

def check_media_server_paths(file_path: str, sonarr: Sonarr, radarr: Radarr) -> Dict:
    """Check if path is managed by Sonarr or Radarr."""
    norm_path = _normalize_path(file_path)
    parent_path = str(Path(norm_path).parent)
    
    # Check Sonarr paths
    sonarr_paths = sonarr.get_series_paths()
    if parent_path in sonarr_paths:
        return {
            'server': 'sonarr',
            'managed': True,
            'info': sonarr_paths[parent_path]
        }
    
    # Check Radarr paths
    radarr_paths = radarr.get_movie_paths()
    if parent_path in radarr_paths:
        return {
            'server': 'radarr',
            'managed': True,
            'info': radarr_paths[parent_path]
        }
    
    return {'managed': False}

def format_size_bytes(size_str: str) -> int:
    """Convert size string to bytes."""
    if size_str == 'Unknown':
        return 0
    
    units = {'B': 1, 'KB': 1024, 'MB': 1024**2, 'GB': 1024**3, 'TB': 1024**4}
    number, unit = size_str.split()
    return int(float(number) * units[unit])

def create_cleanup_script(groups: List[Dict], sonarr: Sonarr, radarr: Radarr, output_file: str):
    """Create a shell script to handle the duplicates."""
    with open(output_file, 'w') as f:
        f.write('#!/bin/bash\n\n')
        f.write('# Duplicate cleanup script\n')
        f.write('# Generated based on Sonarr/Radarr managed paths\n\n')
        
        f.write('# Function to safely remove files\n')
        f.write('safe_remove() {\n')
        f.write('    file="$1"\n')
        f.write('    if [ -f "$file" ]; then\n')
        f.write('        echo "Removing: $file"\n')
        f.write('        rm "$file"\n')
        f.write('    else\n')
        f.write('        echo "File not found: $file"\n')
        f.write('    fi\n')
        f.write('}\n\n')
        
        for i, group in enumerate(groups, 1):
            f.write(f'\necho "Processing group {i}..."\n')
            
            # Check each file against Sonarr/Radarr
            base_files_info = [
                (file, size, check_media_server_paths(file, sonarr, radarr))
                for file, size in group['base_files']
            ]
            
            similar_files_info = [
                (file, size, check_media_server_paths(file, sonarr, radarr))
                for file, size in group['similar_files']
            ]
            
            base_managed = any(info['managed'] for _, _, info in base_files_info)
            similar_managed = any(info['managed'] for _, _, info in similar_files_info)
            
            f.write(f'\n# Group {i}: {group["base_folder"]}\n')
            
            # Write detailed information about each file
            f.write('\n# Base files:\n')
            for file, size, info in base_files_info:
                status = f"MANAGED by {info['server']}" if info['managed'] else "UNMANAGED"
                f.write(f'# {file} ({size}) - {status}\n')
            
            f.write('\n# Similar files:\n')
            for file, size, info in similar_files_info:
                status = f"MANAGED by {info['server']}" if info['managed'] else "UNMANAGED"
                f.write(f'# {file} ({size}) - {status}\n')
            
            # Decision logic
            if base_managed and not similar_managed:
                f.write('\n# Keeping base files (managed by Sonarr/Radarr) and removing duplicates\n')
                for file, _, _ in similar_files_info:
                    f.write(f'safe_remove "{file}"\n')
            elif similar_managed and not base_managed:
                f.write('\n# Keeping similar files (managed by Sonarr/Radarr) and removing duplicates\n')
                for file, _, _ in base_files_info:
                    f.write(f'safe_remove "{file}"\n')
            else:
                f.write('\n# Manual review required - multiple or no managed versions found\n')
                if base_managed and similar_managed:
                    f.write('# Both versions are managed by Sonarr/Radarr\n')
                else:
                    f.write('# No versions are managed by Sonarr/Radarr\n')
                
                # If sizes are available, suggest keeping the larger file
                base_sizes = [format_size_bytes(size) for _, size, _ in base_files_info]
                similar_sizes = [format_size_bytes(size) for _, size, _ in similar_files_info]
                
                if base_sizes and similar_sizes:
                    max_base = max(base_sizes)
                    max_similar = max(similar_sizes)
                    if max_base > max_similar * 1.5:
                        f.write('# Suggestion: Keep base files (larger size)\n')
                    elif max_similar > max_base * 1.5:
                        f.write('# Suggestion: Keep similar files (larger size)\n')
            
            f.write('\n')

def main():
    parser = argparse.ArgumentParser(description='Create cleanup script from duplicate report')
    parser.add_argument('report', help='Path to the duplicate report file')
    parser.add_argument('--output', default='cleanup_script.sh',
                      help='Output cleanup script path')
    parser.add_argument('--force', action='store_true',
                      help='Generate script even without Sonarr/Radarr configuration')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.report):
        logger.error(f"Report file not found: {args.report}")
        sys.exit(1)
    
    try:
        config = load_config()
        sonarr = Sonarr(config['sonarr']['url'], config['sonarr']['api_key'])
        radarr = Radarr(config['radarr']['url'], config['radarr']['api_key'])
    except Exception as e:
        if not args.force:
            logger.error(f"Error loading configuration: {e}")
            sys.exit(1)
        else:
            logger.warning("Running without Sonarr/Radarr integration")
            sonarr = None
            radarr = None
    
    groups = parse_report(args.report)
    create_cleanup_script(groups, sonarr, radarr, args.output)
    
    logger.info(f"Cleanup script created: {args.output}")
    logger.info("Please review the script carefully before running it!")
    logger.info("The script includes safety checks and detailed comments.")

if __name__ == '__main__':
    main() 