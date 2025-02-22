# Media Duplicate Finder

A tool to find and clean up duplicate media files across multiple drives, with integration for Sonarr and Radarr.

## Features

- Scans multiple drives for duplicate media files
- Smart matching using folder names and media information
- Integration with Sonarr and Radarr for intelligent cleanup
- Handles different quality versions of the same media
- Safe cleanup with preview and confirmation

## Installation

1. Clone the repository:
```bash
git clone https://github.com/gokudoku/media-duplicate-finder.git
cd media-duplicate-finder
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure Sonarr/Radarr integration (optional):
```bash
mkdir -p ~/.config/media_cleanup
cp config.example.json ~/.config/media_cleanup/config.json
# Edit config.json with your API keys
```

## Usage

1. Find duplicates:
```bash
python src/find_dupes.py --scan-mounts
```

2. Generate cleanup script:
```bash
python src/cleanup_dupes.py duplicate_report.txt
```

3. Review and run cleanup:
```bash
# Review the generated script
less cleanup_script.sh
# Make it executable
chmod +x cleanup_script.sh
# Run it
./cleanup_script.sh
```

## Configuration

### Sonarr/Radarr Integration

Edit `~/.config/media_cleanup/config.json`:
```json
{
    "sonarr": {
        "url": "http://localhost:8989",
        "api_key": "your_sonarr_api_key"
    },
    "radarr": {
        "url": "http://localhost:7878",
        "api_key": "your_radarr_api_key"
    }
}
```

## Contributing

Pull requests are welcome! For major changes, please open an issue first to discuss what you would like to change.

## License

[MIT](https://choosealicense.com/licenses/mit/) 