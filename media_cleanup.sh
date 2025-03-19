#!/bin/bash

# Media Server Duplicate Finder and Cleanup Script
# This script automates finding and cleaning up duplicate media folders on your media server

# Set script directory to allow running from anywhere
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Color definitions
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to check if python requirements are installed
check_python_requirements() {
    if [ ! -f "requirements.txt" ]; then
        echo -e "${RED}Error: requirements.txt not found!${NC}"
        echo "Please ensure you're running this script from the correct directory."
        exit 1
    fi
    
    echo -e "${BLUE}Checking Python requirements...${NC}"
    python3 -m pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo -e "${RED}Error installing Python requirements.${NC}"
        echo "Please check your Python installation and try again."
        exit 1
    fi
    echo -e "${GREEN}Python requirements installed successfully.${NC}"
}

# Function to check for configuration files
check_config_files() {
    if [ ! -f "config.json" ]; then
        echo -e "${YELLOW}Warning: config.json not found!${NC}"
        echo "Creating a sample config file..."
        cat > config.json <<EOL
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
EOL
        echo -e "${YELLOW}Please edit config.json with your actual API keys before proceeding.${NC}"
        return 1
    fi
    
    if [ ! -f ".env" ]; then
        echo -e "${YELLOW}Warning: .env file not found!${NC}"
        echo "Creating a sample .env file..."
        cat > .env <<EOL
# Docker Compose configuration
DOCKER_COMPOSE_FILE=/path/to/your/docker-compose.yml
EOL
        echo -e "${YELLOW}Please edit .env with your actual paths before proceeding.${NC}"
        return 1
    fi
    
    if [ ! -f "protected_dirs.json" ]; then
        echo -e "${YELLOW}Warning: protected_dirs.json not found!${NC}"
        echo "Creating a sample protected directories file..."
        cat > protected_dirs.json <<EOL
{
  "protected_dirs": [
    "/media/Movies",
    "/media/Television",
    "/media/TV",
    "/mnt/media/Movies",
    "/mnt/storage/TV"
  ],
  "comment": "Add your media root folders here to prevent them from being treated as duplicates. These directories and their immediate subdirectories will not be deleted by the cleanup script."
}
EOL
        echo -e "${YELLOW}Please edit protected_dirs.json with your actual protected directories before proceeding.${NC}"
        return 1
    fi
    
    return 0
}

# Function to show help
show_help() {
    echo "Usage: $0 [options]"
    echo ""
    echo "This script finds and helps clean up duplicate media folders on your media server."
    echo ""
    echo "Options:"
    echo "  --find-only            Only run the duplicate finding step without cleanup"
    echo "  --cleanup-only         Only run the cleanup step using existing report"
    echo "  --dry-run              Simulate deletions without actually removing files"
    echo "  --auto                 Run cleanup in automated mode (no prompts)"
    echo "  --filter=PATTERN       Filter duplicates containing PATTERN (e.g. Movies)"
    echo "  --min-size=SIZE        Only delete duplicates larger than SIZE in bytes"
    echo "  --max-size=SIZE        Only delete duplicates smaller than SIZE in bytes"
    echo "  --help                 Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                     # Run full process (find duplicates then cleanup)"
    echo "  $0 --find-only         # Only find duplicates, don't clean up yet"
    echo "  $0 --cleanup-only --dry-run --filter=Movies  # Simulate cleanup for movie duplicates"
}

# Parse command line arguments
FIND_ONLY=false
CLEANUP_ONLY=false
DRY_RUN=false
AUTO_MODE=false
FILTER_PATTERN=""
MIN_SIZE=""
MAX_SIZE=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --help)
            show_help
            exit 0
            ;;
        --find-only)
            FIND_ONLY=true
            shift
            ;;
        --cleanup-only)
            CLEANUP_ONLY=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --auto)
            AUTO_MODE=true
            shift
            ;;
        --filter=*)
            FILTER_PATTERN="${1#*=}"
            shift
            ;;
        --min-size=*)
            MIN_SIZE="${1#*=}"
            shift
            ;;
        --max-size=*)
            MAX_SIZE="${1#*=}"
            shift
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            show_help
            exit 1
            ;;
    esac
done

# Display header
echo -e "${BLUE}=== Media Server Duplicate Finder and Cleanup ===${NC}"
echo "This script will help you identify and clean up duplicate media folders."
echo ""

# Check for Python requirements
check_python_requirements

# Check configuration files
check_config_files
if [ $? -ne 0 ]; then
    echo -e "${YELLOW}Please update the configuration files before continuing.${NC}"
    read -p "Press Enter to continue (or Ctrl+C to exit)..."
fi

# Run the duplicate finder unless in cleanup-only mode
if [ "$CLEANUP_ONLY" = false ]; then
    echo -e "${BLUE}Step 1: Finding duplicate media folders...${NC}"
    echo "This will scan your media folders and identify potential duplicates."
    echo "Scanning can take some time depending on the size of your media library."
    
    # Ensure output directory exists
    mkdir -p output
    
    # Run the quick duplicate finder script
    python3 quick_duplicate_finder.py
    
    if [ $? -ne 0 ]; then
        echo -e "${RED}Error: Duplicate finder script failed.${NC}"
        echo "Please check the output above for errors."
        exit 1
    fi
    
    # Process the unofficial paths to get official paths
    echo -e "${BLUE}Step 2: Determining official media paths...${NC}"
    echo "This will verify which paths are managed by Sonarr/Radarr vs which are duplicates."
    
    python3 get_official_paths.py
    
    if [ $? -ne 0 ]; then
        echo -e "${RED}Error: Official paths script failed.${NC}"
        echo "Please check the output above for errors."
        exit 1
    fi
    
    echo -e "${GREEN}Duplicate detection complete!${NC}"
    echo "A report file has been generated with verified duplicates."
    
    # If in find-only mode, stop here
    if [ "$FIND_ONLY" = true ]; then
        echo -e "${YELLOW}Find-only mode selected. Stopping after duplicate detection.${NC}"
        echo "Run '$0 --cleanup-only' to process the duplicates."
        exit 0
    fi
fi

# Run the cleanup script
echo -e "${BLUE}Step 3: Cleaning up duplicate media folders...${NC}"

# Build the cleanup command
CLEANUP_CMD="./enhanced_cleanup_v2.sh"

if [ "$DRY_RUN" = true ]; then
    CLEANUP_CMD="$CLEANUP_CMD --dry-run"
fi

if [ "$AUTO_MODE" = true ]; then
    CLEANUP_CMD="$CLEANUP_CMD --auto"
fi

if [ -n "$FILTER_PATTERN" ]; then
    CLEANUP_CMD="$CLEANUP_CMD --filter=$FILTER_PATTERN"
fi

if [ -n "$MIN_SIZE" ]; then
    CLEANUP_CMD="$CLEANUP_CMD --min-size=$MIN_SIZE"
fi

if [ -n "$MAX_SIZE" ]; then
    CLEANUP_CMD="$CLEANUP_CMD --max-size=$MAX_SIZE"
fi

echo "Running cleanup with: $CLEANUP_CMD"
bash -c "$CLEANUP_CMD"

# Display completion message
echo -e "${GREEN}Media duplicate cleanup process completed!${NC}"
echo "Check the log files for detailed information about the cleanup operation."
echo ""
echo -e "${YELLOW}IMPORTANT: If you ran with --dry-run, no files were actually deleted.${NC}"
echo "Review the logs and run without --dry-run to perform actual deletions." 