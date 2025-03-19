# Media Duplicate Finder and Cleanup

A comprehensive suite of tools for finding and safely cleaning up duplicate media folders on your media server. This project helps you reclaim disk space by identifying and removing duplicate copies of movies, TV shows, and other media content while preserving your primary media library.

## Features

- **Intelligent Duplicate Detection**: Uses metadata from Sonarr/Radarr to identify your primary media collection
- **Docker-Compose Integration**: Automatically detects media paths from your docker-compose configuration
- **Robust Safety Mechanisms**: Multiple layers of protection to prevent accidental deletion of important data
- **Interactive Mode**: Review and confirm each deletion to ensure only the right files are removed
- **Dry Run Mode**: Simulate the cleanup process without actually deleting anything
- **Flexible Filtering**: Target specific directories or size ranges for cleanup
- **Detailed Logging**: Comprehensive logs of all actions with timestamps

## Prerequisites

- Python 3.6+
- Bash shell environment
- Sonarr and/or Radarr set up (recommended for best results)
- Docker-based media server (recommended but not required)

## Installation

1. Clone the repository to your media server:
   ```
   git clone https://github.com/yourusername/media-duplicate-finder.git
   cd media-duplicate-finder
   ```

2. Install the required Python packages:
   ```
   pip install -r requirements.txt
   ```

3. Configure your API keys and paths:
   - Copy `config.json.example` to `config.json` and add your Sonarr/Radarr API keys
   - Copy `.env.example` to `.env` and set your Docker compose file path
   - Update `protected_dirs.json` with your primary media directories that should never be deleted

## Configuration

### config.json

This file stores your Sonarr and Radarr API connection information:

```json
{
    "sonarr": {
        "url": "http://localhost:8989",
        "api_key": "your_sonarr_api_key_here"
    },
    "radarr": {
        "url": "http://localhost:7878", 
        "api_key": "your_radarr_api_key_here"
    }
}
```

### .env

This file specifies the location of your docker-compose file:

```
DOCKER_COMPOSE_FILE=/path/to/your/docker-compose.yml
```

### protected_dirs.json

This file defines directories that should never be deleted:

```json
{
  "protected_dirs": [
    "/media/Movies",
    "/media/Television",
    "/media/TV",
    "/mnt/media/Movies",
    "/mnt/storage/TV"
  ]
}
```

## Usage

The main script `media_cleanup.sh` combines all the tools into a single workflow.

### Basic Usage

Run the full process (find duplicates, then cleanup interactively):

```
./media_cleanup.sh
```

Find duplicates without cleanup:

```
./media_cleanup.sh --find-only
```

Run cleanup in simulation mode:

```
./media_cleanup.sh --dry-run
```

### Advanced Usage

```
./media_cleanup.sh --cleanup-only --dry-run --filter=Movies
```

This will:
1. Skip the duplicate finding step (use existing report)
2. Run in dry-run mode (no actual deletions)
3. Only process directories containing "Movies"

### Command Line Options

- `--find-only`: Only run the duplicate finding step without cleanup
- `--cleanup-only`: Only run the cleanup step using existing report
- `--dry-run`: Simulate deletions without actually removing files
- `--auto`: Run cleanup in automated mode (no prompts)
- `--filter=PATTERN`: Filter duplicates containing PATTERN (e.g., Movies)
- `--min-size=SIZE`: Only delete duplicates larger than SIZE in bytes
- `--max-size=SIZE`: Only delete duplicates smaller than SIZE in bytes
- `--help`: Show the help message

## How It Works

The system works in three steps:

1. **Quick Duplicate Finder (`quick_duplicate_finder.py`)**:
   - Scans your media directories to find potential duplicate folders
   - Uses folder naming patterns and docker-compose configuration to identify media paths
   - Generates a preliminary report of potential duplicates

2. **Official Path Verification (`get_official_paths.py`)**:
   - Uses Sonarr/Radarr APIs to determine your "official" media paths
   - Verifies which paths are part of your managed libraries
   - Generates an updated report with verified official paths and duplicates

3. **Enhanced Cleanup (`enhanced_cleanup_v2.sh`)**:
   - Processes the verified duplicate report
   - Implements multiple safety checks to prevent accidental deletions
   - Allows for interactive or automated cleanup
   - Generates detailed logs of all actions

## Safety Features

The system includes multiple layers of protection:

- **Protected Directories**: Paths listed in `protected_dirs.json` are never deleted
- **Media Type Matching**: Prevents deleting TV shows marked as duplicates of movies
- **Size and Content Verification**: Warns about suspicious size differences
- **Interactive Confirmation**: Requires explicit confirmation for each deletion in interactive mode
- **Dry Run Mode**: Allows simulating the entire process before committing to deletions

## Logging

All operations are logged with timestamps:

- **Cleanup Session Logs**: Detailed logs of each cleanup session saved with timestamps
- **Summary Reports**: Condensed reports showing deleted items and space saved

## Troubleshooting

### Common Issues

1. **Script can't find docker-compose file**:
   - Ensure your `.env` file has the correct path
   - The script will look in common locations if not specified

2. **API connection errors**:
   - Verify your API keys in `config.json`
   - Check that Sonarr/Radarr are running and accessible

3. **No duplicates found**:
   - Check that your media paths are correctly mapped in your docker setup
   - The script may not detect duplicates if they don't follow common naming patterns

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Disclaimer

Always run with `--dry-run` first to verify what would be deleted. The author is not responsible for any data loss resulting from the use of these tools. 