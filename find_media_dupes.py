#!/usr/bin/env python3

import os
import sys
from pathlib import Path
import hashlib
from collections import defaultdict
import difflib
import argparse
from typing import Dict, List, Set, Tuple
import logging
from tqdm import tqdm
import re
import stat
import psutil

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def is_mount_point(path: Path) -> bool:
    """Check if the given path is a mount point."""
    try:
        return path.is_mount()
    except:
        return False

def get_mountpoints() -> List[str]:
    """Get all mounted filesystems."""
    return [p.mountpoint for p in psutil.disk_partitions(all=True)
            if not any(x in p.mountpoint for x in ['/boot', '/proc', '/sys', '/dev'])]

def has_read_permission(path: Path) -> bool:
    """Check if we have read permission for the path."""
    try:
        return os.access(str(path), os.R_OK)
    except:
        return False

def clean_media_name(name: str) -> str:
    """
    Clean up a media name by removing common patterns, years, quality indicators, etc.
    Example: "The.Movie.2160p.HDR.BluRay.x265-GROUP" -> "the movie"
    """
    # Remove year patterns like (2020) or .2020. or [2020]
    name = re.sub(r'[\(\[\.](?:19|20)\d{2}[\)\]\.]?', '', name)
    
    # Remove common quality and source patterns
    patterns_to_remove = [
        r'2160p', r'1080p', r'720p', r'480p', 
        r'bluray', r'web-?dl', r'webrip', r'hdtv', r'dvdrip',
        r'x264', r'x265', r'hevc', r'xvid', r'divx',
        r'aac\d?', r'ac3', r'dts', r'hdma',
        r'repack', r'proper', r'extended',
        r'hdr\d*', r'dv', r'dolby', r'atmos',
        r'-[a-zA-Z0-9]+$',  # Release group at the end
        r'\bdc\b',  # Directors cut
        r'remux',
        r'dubbed',
        r'\bws\b',  # Wide Screen
        r'retail',
        r'cd[0-9]',
        r'disc[0-9]',
        r'complete'
    ]
    
    for pattern in patterns_to_remove:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE)
    
    # Replace dots, underscores, and other separators with spaces
    name = re.sub(r'[._-]', ' ', name)
    
    # Remove multiple spaces and trim
    name = ' '.join(name.split())
    
    return name.lower().strip()

def get_file_hash(filepath: str, block_size: int = 65536) -> str:
    """Calculate SHA256 hash of a file in chunks to handle large files efficiently."""
    sha256 = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            for block in iter(lambda: f.read(block_size), b''):
                sha256.update(block)
        return sha256.hexdigest()
    except (PermissionError, OSError) as e:
        logger.error(f"Error hashing {filepath}: {e}")
        return None

def get_parent_folder_name(filepath: Path) -> str:
    """Get the immediate parent folder name of a file."""
    try:
        return filepath.parent.name
    except:
        return ""

def get_grandparent_folder_name(filepath: Path) -> str:
    """Get the parent of the parent folder name, useful for nested media structures."""
    try:
        return filepath.parent.parent.name
    except:
        return ""

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
                # Compare cleaned names
                ratio = difflib.SequenceMatcher(None, clean_name1, clean_name2).ratio()
                
                # Higher threshold since we're comparing cleaned names
                if ratio > 0.9:
                    current_group['similar_folders'].append(folder2)
                    current_group['similarity_scores'].append(ratio)
                    current_group['files'].extend(folder_map[folder2])
                    processed.add(folder2)
        
        if current_group['similar_folders']:
            processed.add(folder1)
            similar_groups.append(current_group)
    
    return similar_groups

def scan_for_duplicates(root_paths: List[str]) -> Dict:
    """
    Scan directories for potential duplicate media files based on folder structure
    and naming patterns.
    """
    # Initialize data structures
    folder_map = defaultdict(list)  # folder name -> list of files
    
    logger.info("Starting scan of directories...")
    
    # Verify all paths are accessible
    valid_paths = []
    for root_path in root_paths:
        root = Path(root_path).resolve()
        if not root.exists():
            logger.warning(f"Path {root_path} does not exist, skipping...")
            continue
        if not has_read_permission(root):
            logger.warning(f"No read permission for {root_path}, skipping...")
            continue
        valid_paths.append(root)
    
    if not valid_paths:
        logger.error("No valid paths to scan!")
        return []
    
    # Scan all directories and collect video files
    for root in valid_paths:
        logger.info(f"Scanning {root}...")
        try:
            for filepath in tqdm(list(root.rglob('*')), desc=f"Scanning {root}"):
                if filepath.is_file() and is_video_file(str(filepath)):
                    try:
                        if has_read_permission(filepath):
                            # Store both by immediate parent and grandparent folder names
                            parent = get_parent_folder_name(filepath)
                            grandparent = get_grandparent_folder_name(filepath)
                            
                            if parent:
                                folder_map[parent].append(filepath)
                            if grandparent:
                                folder_map[grandparent].append(filepath)
                    except (PermissionError, OSError) as e:
                        logger.error(f"Error accessing {filepath}: {e}")
        except (PermissionError, OSError) as e:
            logger.error(f"Error scanning directory {root}: {e}")

    # Find potential duplicates
    logger.info("Analyzing folder names and structures...")
    similar_groups = find_similar_media_folders(folder_map)
    
    return similar_groups

def format_size(size_bytes: int) -> str:
    """Format file size in human readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} TB"

def main():
    parser = argparse.ArgumentParser(description='Find duplicate media files across directories')
    parser.add_argument('directories', nargs='*', help='Directories to scan')
    parser.add_argument('--output', type=str, default='duplicate_report.txt',
                      help='Output file for the duplicate report')
    parser.add_argument('--scan-mounts', action='store_true',
                      help='Automatically scan all mounted filesystems')
    
    args = parser.parse_args()
    
    scan_paths = []
    if args.scan_mounts:
        scan_paths.extend(get_mountpoints())
        logger.info(f"Found mount points: {', '.join(scan_paths)}")
    
    if args.directories:
        scan_paths.extend(args.directories)
    
    if not scan_paths:
        logger.error("No directories specified and --scan-mounts not used!")
        parser.print_help()
        sys.exit(1)
    
    results = scan_for_duplicates(scan_paths)
    
    # Write report
    with open(args.output, 'w', encoding='utf-8') as f:
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
            
            f.write("\n")
    
    logger.info(f"Report written to {args.output}")

if __name__ == '__main__':
    main() 