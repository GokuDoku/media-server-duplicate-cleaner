#!/usr/bin/env python3

import os
import sys
from pathlib import Path
import hashlib
from collections import defaultdict
import difflib
import argparse
import logging
from tqdm import tqdm
import re
import stat
import psutil
import concurrent.futures
from typing import Dict, List, Set, Tuple

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_media_mountpoints():
    """Get all mounted media filesystems."""
    media_mounts = []
    for p in psutil.disk_partitions(all=True):
        # Only include /media mounts and skip system paths
        if p.mountpoint.startswith('/media/'):
            media_mounts.append(p.mountpoint)
    return media_mounts

def clean_media_name(name: str) -> str:
    """Clean up a media name by removing common patterns."""
    name = re.sub(r'[\(\[\.](?:19|20)\d{2}[\)\]\.]?', '', name)
    patterns_to_remove = [
        r'2160p', r'1080p', r'720p', r'480p', 
        r'bluray', r'web-?dl', r'webrip', r'hdtv', r'dvdrip',
        r'x264', r'x265', r'hevc', r'xvid', r'divx',
        r'aac\d?', r'ac3', r'dts', r'hdma',
        r'repack', r'proper', r'extended',
        r'hdr\d*', r'dv', r'dolby', r'atmos',
        r'-[a-zA-Z0-9]+$',
        r'\bdc\b', r'remux', r'dubbed',
        r'\bws\b', r'retail',
        r'cd[0-9]', r'disc[0-9]', r'complete'
    ]
    for pattern in patterns_to_remove:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE)
    name = re.sub(r'[._-]', ' ', name)
    return ' '.join(name.split()).lower().strip()

def scan_directory(root: Path) -> Dict[str, List[Path]]:
    """Scan a single directory for media files."""
    folder_map = defaultdict(list)
    try:
        for filepath in tqdm(list(root.rglob('*')), desc=f"Scanning {root}", leave=False):
            if filepath.is_file() and is_video_file(str(filepath)):
                try:
                    parent = filepath.parent.name
                    grandparent = filepath.parent.parent.name
                    
                    if parent:
                        folder_map[parent].append(filepath)
                    if grandparent:
                        folder_map[grandparent].append(filepath)
                except Exception as e:
                    logger.error(f"Error accessing {filepath}: {e}")
    except Exception as e:
        logger.error(f"Error scanning directory {root}: {e}")
    return folder_map

def is_video_file(filepath: str) -> bool:
    """Check if file is a video file based on extension."""
    video_extensions = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v'}
    return Path(filepath).suffix.lower() in video_extensions

def find_similar_media_folders(folder_map: Dict[str, List[Path]]) -> List[Dict]:
    """Find potentially duplicate media based on folder name similarity."""
    similar_groups = []
    processed = set()
    
    # Convert folder names to cleaned versions for comparison
    cleaned_folders = {
        folder: clean_media_name(folder)
        for folder in folder_map.keys()
    }
    
    for folder1, clean_name1 in cleaned_folders.items():
        if folder1 in processed or not clean_name1:
            continue
            
        current_group = {
            'base_folder': folder1,
            'similar_folders': [],
            'similarity_scores': [],
            'files': folder_map[folder1]
        }
        
        for folder2, clean_name2 in cleaned_folders.items():
            if folder1 != folder2 and folder2 not in processed and clean_name2:
                ratio = difflib.SequenceMatcher(None, clean_name1, clean_name2).ratio()
                if ratio > 0.9:
                    current_group['similar_folders'].append(folder2)
                    current_group['similarity_scores'].append(ratio)
                    processed.add(folder2)
        
        if current_group['similar_folders']:
            processed.add(folder1)
            similar_groups.append(current_group)
    
    return similar_groups

def format_size(size_bytes: int) -> str:
    """Format file size in human readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} TB"

def scan_for_duplicates(root_paths: List[str], max_workers: int = None) -> List[Dict]:
    """
    Scan directories for potential duplicate media files using parallel processing.
    """
    logger.info("Starting scan of directories...")
    
    # Filter valid paths
    valid_paths = [Path(p) for p in root_paths if Path(p).exists()]
    
    if not valid_paths:
        logger.error("No valid paths to scan!")
        return []
    
    # Use parallel processing to scan directories
    folder_map = defaultdict(list)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_path = {
            executor.submit(scan_directory, path): path
            for path in valid_paths
        }
        
        for future in concurrent.futures.as_completed(future_to_path):
            path = future_to_path[future]
            try:
                result = future.result()
                for folder, files in result.items():
                    folder_map[folder].extend(files)
            except Exception as e:
                logger.error(f"Error processing {path}: {e}")
    
    logger.info("Analyzing folder names and structures...")
    return find_similar_media_folders(folder_map)

def write_report(results: List[Dict], output_file: str):
    """Write the duplicate report to a file."""
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("=== Potential Duplicate Media Report ===\n\n")
        
        if not results:
            f.write("No potential duplicates found.\n")
            return
            
        for group in results:
            f.write("\n" + "="*50 + "\n")
            f.write(f"Base Folder: {group['base_folder']}\n")
            f.write("Files:\n")
            for file in group['files']:
                try:
                    size = format_size(file.stat().st_size)
                    f.write(f"  {file} ({size})\n")
                except:
                    f.write(f"  {file}\n")
            
            for folder, score in zip(group['similar_folders'], group['similarity_scores']):
                f.write(f"\nSimilar Folder (similarity: {score:.2f}): {folder}\n")
                for file in folder_map[folder]:
                    try:
                        size = format_size(file.stat().st_size)
                        f.write(f"  {file} ({size})\n")
                    except:
                        f.write(f"  {file}\n")

def main():
    parser = argparse.ArgumentParser(description='Find duplicate media files across directories')
    parser.add_argument('directories', nargs='*', help='Directories to scan')
    parser.add_argument('--output', type=str, default='duplicate_report.txt',
                      help='Output file for the duplicate report')
    parser.add_argument('--scan-mounts', action='store_true',
                      help='Automatically scan all mounted media filesystems')
    parser.add_argument('--threads', type=int, default=None,
                      help='Number of threads to use for scanning (default: CPU count)')
    
    args = parser.parse_args()
    
    scan_paths = []
    if args.scan_mounts:
        scan_paths.extend(get_media_mountpoints())
        logger.info(f"Found media mount points: {', '.join(scan_paths)}")
    
    if args.directories:
        scan_paths.extend(args.directories)
    
    if not scan_paths:
        logger.error("No directories specified and --scan-mounts not used!")
        parser.print_help()
        sys.exit(1)
    
    results = scan_for_duplicates(scan_paths, max_workers=args.threads)
    write_report(results, args.output)
    
    logger.info(f"Report written to {args.output}")

if __name__ == '__main__':
    main() 