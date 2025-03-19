#!/bin/bash

# Enhanced Media Duplicates Cleanup Script (v2)
# This script safely cleans up duplicate media folders based on a report.

# Default values
DRY_RUN=false
AUTO_MODE=false
FILTER_PATTERN=""
MIN_SIZE=0
MAX_SIZE=9223372036854775807  # Default to max possible value (2^63-1)
REPORT_FILE="updated_duplicate_report_fixed.txt"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Generate timestamp for logs
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="cleanup_session_${TIMESTAMP}.log"
SUMMARY_REPORT="cleanup_summary_${TIMESTAMP}.txt"

# Log counters
TOTAL_FOLDERS=0
SKIPPED_FOLDERS=0
SAFETY_WARNINGS=0
DUPLICATES_PROCESSED=0
TOTAL_SAVED=0
SIZE_SKIPPED=0

# Function to display help
show_help() {
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  --dry-run               Simulate deletions without actually deleting files"
    echo "  --auto                  Run in automated mode (no confirmations)"
    echo "  --filter=PATTERN        Only process directories containing PATTERN"
    echo "  --min-size=SIZE         Only delete duplicates larger than SIZE in bytes"
    echo "  --max-size=SIZE         Only delete duplicates smaller than SIZE in bytes"
    echo "  --report=FILE           Specify the duplicate report file to use"
    echo "  --help                  Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 --dry-run                       # Interactive simulation mode"
    echo "  $0 --auto --dry-run                # Automated simulation mode"
    echo "  $0 --filter=Movies                 # Only process paths with 'Movies'"
    echo "  $0 --min-size=1000000000           # Only delete duplicates larger than 1GB"
    echo "  $0 --report=my_report.txt          # Use a custom report file"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --help)
            show_help
            exit 0
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
        --report=*)
            REPORT_FILE="${1#*=}"
            shift
            ;;
        *)
            # Unknown option
            echo "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Function to log messages to both console and log file
log_status() {
    echo -e "$1"
    echo "$1" | sed 's/\x1B\[[0-9;]*[mK]//g' >> "$LOG_FILE"
}

# Function to format file size
format_size() {
    local size=$1
    
    if (( size > 1099511627776 )); then
        printf "%.0f TB" "$(echo "$size/1099511627776" | bc -l)"
    elif (( size > 1073741824 )); then
        printf "%.0f GB" "$(echo "$size/1073741824" | bc -l)"
    elif (( size > 1048576 )); then
        printf "%.0f MB" "$(echo "$size/1048576" | bc -l)"
    elif (( size > 1024 )); then
        printf "%.0f KB" "$(echo "$size/1024" | bc -l)"
    else
        printf "%d B" "$size"
    fi
}

# Function to check if a path matches the filter
matches_filter() {
    local path=$1
    
    if [ -z "$FILTER_PATTERN" ]; then
        return 0  # No filter set, match everything
    fi
    
    if [[ "$path" == *"$FILTER_PATTERN"* ]]; then
        return 0  # Path contains the filter pattern
    fi
    
    return 1  # No match
}

# Function to check if size is within allowed range
size_in_range() {
    local size=$1
    
    if (( size < MIN_SIZE )); then
        return 1  # Size is below minimum
    fi
    
    if (( size > MAX_SIZE )); then
        return 1  # Size is above maximum
    fi
    
    return 0  # Size is within range
}

# Function to count video files in a directory
count_video_files() {
    local dir="$1"
    find "$dir" -type f \( -name "*.mkv" -o -name "*.mp4" -o -name "*.avi" -o -name "*.mov" -o -name "*.wmv" -o -name "*.m4v" \) 2>/dev/null | wc -l
}

# Function to get directory size in bytes
get_dir_size_bytes() {
    local dir="$1"
    du -b -s "$dir" 2>/dev/null | cut -f1
}

# Function to get human-readable directory size
get_dir_size_human() {
    local dir="$1"
    local size=$(get_dir_size_bytes "$dir")
    format_size "$size"
}

# Function to show a warning
show_warning() {
    log_status "${YELLOW}⚠️ $1${NC}"
    ((SAFETY_WARNINGS++))
}

# Function to check if a directory is potentially dangerous to delete
is_protected_directory() {
    local path="$1"
    
    # Load protected directories from config
    local config_file="protected_dirs.json"
    if [[ -f "$config_file" ]]; then
        while IFS= read -r protected_dir; do
            # Remove quotes and commas
            protected_dir=$(echo "$protected_dir" | sed 's/[",]//g' | xargs)
            
            # Skip empty lines or comments
            if [[ -z "$protected_dir" || "$protected_dir" == *"comment"* ]]; then
                continue
            fi
            
            # Check if this path is a protected directory or its direct subdirectory
            if [[ "$path" == "$protected_dir" || "$path" == "$protected_dir"/* ]]; then
                # Only protect directories with media-related names
                base_dir=$(basename "$path")
                if [[ "${base_dir,,}" == "movies" || 
                      "${base_dir,,}" == "tv" || 
                      "${base_dir,,}" == "television" || 
                      "${base_dir,,}" == "films" || 
                      "${base_dir,,}" == "videos" ]]; then
                    return 0  # Protected directory
                fi
            fi
        done < <(grep '"/' "$config_file")
    fi
    
    # Check for common system directories that should never be deleted
    if [[ "$path" == "/" || 
          "$path" == "/home" || 
          "$path" == "/usr" || 
          "$path" == "/var" || 
          "$path" == "/etc" || 
          "$path" == "/boot" ]]; then
        return 0  # System directory
    fi
    
    return 1  # Not protected
}

# Function to check if the media types match 
# (to avoid deleting TV shows marked as duplicates of movies)
match_media_type() {
    local path1="$1"
    local path2="$2"
    
    # Extract parent directory names
    local parent1=$(basename "$(dirname "$path1")")
    local parent2=$(basename "$(dirname "$path2")")
    
    # Convert to lowercase
    parent1="${parent1,,}"
    parent2="${parent2,,}"
    
    # Check if both are movies or both are TV
    if [[ ("$parent1" == *"movie"* || "$parent1" == *"film"*) && 
          ("$parent2" == *"movie"* || "$parent2" == *"film"*) ]]; then
        return 0  # Both are movies
    fi
    
    if [[ ("$parent1" == *"tv"* || "$parent1" == *"television"* || "$parent1" == *"series"*) && 
          ("$parent2" == *"tv"* || "$parent2" == *"television"* || "$parent2" == *"series"*) ]]; then
        return 0  # Both are TV shows
    fi
    
    # If parent directories don't match, use fuzzy matching on the paths
    if [[ ("$path1" == *"/movie"* || "$path1" == *"/film"*) && 
          ("$path2" == *"/movie"* || "$path2" == *"/film"*) ]]; then
        return 0  # Both paths contain movie keywords
    fi
    
    if [[ ("$path1" == *"/tv"* || "$path1" == *"/television"* || "$path1" == *"/series"*) && 
          ("$path2" == *"/tv"* || "$path2" == *"/television"* || "$path2" == *"/series"*) ]]; then
        return 0  # Both paths contain TV keywords
    fi
    
    # If we can't determine clearly, assume they match 
    # (this is safest as many paths don't have clear indicators)
    return 0
}

# Show script header
echo "=== Enhanced Media Duplicates Cleanup (v2) ==="
echo "This script uses the verified official paths from a duplicate report file."
echo "Using report: $REPORT_FILE"
echo "Logging to: $LOG_FILE"
echo "Summary report will be saved to: $SUMMARY_REPORT"

if [ "$AUTO_MODE" = true ]; then
    echo "AUTOMATED MODE: Will delete all duplicates without confirmation."
fi

if [ "$DRY_RUN" = true ]; then
    echo "DRY RUN MODE: No files will actually be deleted."
fi

echo "SAFETY CHECKS: Enabled (will warn about large directories and protect critical paths)"
echo ""

# Start logging
log_status "Logging all operations to $LOG_FILE"
log_status "Report file: $REPORT_FILE"

# Log filter settings if any
if [ -n "$FILTER_PATTERN" ]; then
    log_status "Filtering paths containing: $FILTER_PATTERN"
fi

if [ "$MIN_SIZE" -gt 0 ]; then
    log_status "Minimum size for deletion: $(format_size $MIN_SIZE) (${MIN_SIZE} bytes)"
fi

if [ "$MAX_SIZE" -lt 9223372036854775807 ]; then
    log_status "Maximum size for deletion: $(format_size $MAX_SIZE) (${MAX_SIZE} bytes)"
fi

if [ "$AUTO_MODE" = true ]; then
    log_status "Running in AUTOMATED mode (--auto flag detected)"
fi

if [ "$DRY_RUN" = true ]; then
    log_status "Running in DRY RUN mode (--dry-run flag detected)"
fi

# Check if report file exists
if [ ! -f "$REPORT_FILE" ]; then
    log_status "${RED}Error: Report file '$REPORT_FILE' not found!${NC}"
    log_status "Please run get_official_paths.py first to generate the report."
    exit 1
fi

# Parse the report file
log_status "Parsing report file: $REPORT_FILE..."

# Create arrays to store folder information
declare -a folders
declare -a official_paths
declare -a duplicate_paths_array

# Parse the report file
current_folder=""
current_official=""
current_duplicates=""

# Count folders in the report
folder_count=$(grep -c "^Folder: " "$REPORT_FILE")
log_status "Found $folder_count folders with duplicates to process."
echo ""

# Parsing logic
while IFS= read -r line; do
    if [[ $line =~ ^Folder:\ (.*)$ ]]; then
        # If we already have data from a previous entry, save it
        if [ -n "$current_folder" ]; then
            folders+=("$current_folder")
            official_paths+=("$current_official")
            duplicate_paths_array+=("$current_duplicates")
        fi
        # Start a new entry
        current_folder="${BASH_REMATCH[1]}"
        current_official=""
        current_duplicates=""
    elif [[ $line =~ ^Official\ Path.*:\ (.*)$ ]]; then
        current_official="${BASH_REMATCH[1]}"
    elif [[ $line =~ ^\ \ (.*)$ ]] && [[ $line == *"/"* ]] && [[ $line != *"(OFFICIAL)"* ]]; then
        # This is a duplicate path line (not marked as official)
        if [ ! -z "$current_duplicates" ]; then
            current_duplicates+=";"
        fi
        current_duplicates+="${BASH_REMATCH[1]}"
    fi
done < "$REPORT_FILE"

# Add the last entry if there is one
if [ -n "$current_folder" ]; then
    folders+=("$current_folder")
    official_paths+=("$current_official")
    duplicate_paths_array+=("$current_duplicates")
fi

TOTAL_FOLDERS=${#folders[@]}

# Process each duplicate folder
for ((i=0; i<TOTAL_FOLDERS; i++)); do
    folder="${folders[$i]}"
    official="${official_paths[$i]}"
    
    # Skip this folder if it doesn't match the filter pattern
    if ! matches_filter "$official" && ! matches_filter "$folder"; then
        ((SKIPPED_FOLDERS++))
        continue
    fi
    
    log_status "\n${BLUE}===== Processing [$(($i+1))/$TOTAL_FOLDERS]: $folder =====${NC}"
    
    # Check if official path exists
    if [[ "$official" == "Unknown"* ]]; then
        log_status "${RED}WARNING: Official path unknown (not found in Sonarr or Radarr)${NC}"
        log_status "Cannot safely determine which copy to keep. Manual inspection required."
        ((SKIPPED_FOLDERS++))
        continue
    fi
    
    # Check if official path exists
    if [ ! -d "$official" ]; then
        log_status "${RED}WARNING: Official path doesn't exist at: $official${NC}"
        log_status "This is unusual since the path was verified. Manual inspection required."
        log_status "Skipping this folder."
        echo ""
        ((SKIPPED_FOLDERS++))
        continue
    fi
    
    # Get official path details
    official_size_bytes=$(get_dir_size_bytes "$official")
    official_size_human=$(format_size "$official_size_bytes")
    official_videos=$(count_video_files "$official")
    
    log_status "${GREEN}KEEPING: Official location: $official${NC}"
    log_status "  Size: $official_size_human, Video files: $official_videos"
    
    # Split the duplicate paths string into an array
    IFS=';' read -r -a duplicates <<< "${duplicate_paths_array[$i]}"
    
    if [ ${#duplicates[@]} -eq 0 ]; then
        log_status "${YELLOW}No duplicates found for this folder. Skipping...${NC}"
        ((SKIPPED_FOLDERS++))
        continue
    fi
    
    log_status "\n${BLUE}DUPLICATE FOLDERS TO CONSIDER DELETING:${NC}"
    
    # Display duplicate information
    duplicate_warnings=0
    for ((j=0; j<${#duplicates[@]}; j++)); do
        path="${duplicates[$j]}"
        
        # Check if path exists
        if [ ! -d "$path" ]; then
            log_status "  $((j+1)). $path (${RED}Not found${NC})"
            continue
        fi
        
        # Get duplicate path details
        size_bytes=$(get_dir_size_bytes "$path")
        size_human=$(format_size "$size_bytes")
        video_count=$(count_video_files "$path")
        
        # Check for warning conditions
        warnings=""
        
        # Check if size is significantly different
        size_ratio=0
        if [ $official_size_bytes -gt 0 ] && [ $size_bytes -gt 0 ]; then
            if [ $official_size_bytes -gt $size_bytes ]; then
                size_ratio=$(( official_size_bytes / size_bytes ))
            else
                size_ratio=$(( size_bytes / official_size_bytes ))
            fi
            
            if [ $size_ratio -gt 3 ]; then
                warnings+="⚠️ Significant size difference between paths. "
                ((duplicate_warnings++))
            fi
        fi
        
        # Check if video count is significantly different
        if [ $official_videos -gt 0 ] && [ $video_count -gt 0 ]; then
            video_ratio=1
            if [ $official_videos -gt $video_count ]; then
                video_ratio=$(( official_videos / video_count ))
            else
                video_ratio=$(( video_count / official_videos ))
            fi
            
            if [ $video_ratio -gt 2 ]; then
                warnings+="⚠️ Significant difference in video file count. "
                ((duplicate_warnings++))
            fi
        fi
        
        # Check if this is a protected directory
        if is_protected_directory "$path"; then
            warnings+="⚠️ PROTECTED DIRECTORY! "
            ((duplicate_warnings++))
        fi
        
        # Check for media type mismatch
        if ! match_media_type "$official" "$path"; then
            warnings+="⚠️ MEDIA TYPE MISMATCH! "
            ((duplicate_warnings++))
        fi
        
        # Display the duplicate entry with any warnings
        log_status "  $((j+1)). $path (Size: $size_human, Video files: $video_count) $warnings"
    done
    
    # If any warnings were found, show a summary
    if [ $duplicate_warnings -gt 0 ]; then
        log_status "\n${YELLOW}SAFETY WARNING: $duplicate_warnings duplicates are in protected directories or have type mismatches.${NC}"
    fi
    
    # Function to process and optionally delete a duplicate
    process_duplicate() {
        local path="$1"
        local auto_confirm="$2"  # Should be 'true' or 'false'
        
        # Skip if path doesn't exist
        if [ ! -d "$path" ]; then
            log_status "Skipping non-existent path: $path"
            return
        fi
        
        # Check if path matches filter
        if ! matches_filter "$path"; then
            log_status "Skipping path that doesn't match filter: $path"
            return
        }
        
        # Get size in bytes
        local size_bytes=$(get_dir_size_bytes "$path")
        local size_human=$(format_size "$size_bytes")
        
        # Check if size is within allowed range
        if ! size_in_range "$size_bytes"; then
            log_status "Skipping due to size constraints: $path"
            ((SIZE_SKIPPED++))
            return
        }
        
        # Check for protected directories
        if is_protected_directory "$path"; then
            log_status "${YELLOW}SAFETY WARNING: $path is a protected directory!${NC}"
            log_status "Deletion PREVENTED by safety check."
            
            # If in auto mode, ask for override
            if [ "$auto_confirm" = true ]; then
                if [ "$DRY_RUN" = true ]; then
                    log_status "DRY RUN: Would have asked for safety override."
                    log_status "DRY RUN: Would NOT delete $path unless override confirmed."
                else
                    log_status "CONFIRM OVERRIDE: Are you SURE you want to delete this protected directory? (y/n): "
                    read -r override
                    if [ "$override" != "y" ]; then
                        log_status "Deletion cancelled for protected directory."
                        return
                    fi
                    log_status "Override confirmed for protected directory."
                fi
            else
                return
            fi
        }
        
        # Additional safety check for large directories
        if [ "$size_bytes" -gt 100000000000 ]; then  # 100GB
            log_status "${YELLOW}SAFETY WARNING: $path is very large ($size_human)!${NC}"
            
            # If in auto mode, ask for confirmation for large dirs
            if [ "$auto_confirm" = true ]; then
                if [ "$DRY_RUN" = true ]; then
                    log_status "DRY RUN: Would have asked for confirmation for large directory."
                else
                    log_status "This directory is extremely large ($size_human). Are you SURE you want to delete it? (y/n): "
                    read -r large_confirm
                    if [ "$large_confirm" != "y" ]; then
                        log_status "Deletion cancelled for large directory."
                        return
                    fi
                    log_status "Large deletion confirmed."
                fi
            fi
        }
        
        # Proceed with deletion
        log_status "Deleting: $path ($size_human)"
        
        if [ "$DRY_RUN" = true ]; then
            log_status "DRY RUN: Would delete $path"
        else
            # Actual deletion
            rm -rf "$path"
            if [ $? -eq 0 ]; then
                log_status "Deleted: $path ($size_human)"
            else
                log_status "${RED}Error deleting: $path${NC}"
                return
            }
        fi
        
        # Update counters
        ((DUPLICATES_PROCESSED++))
        TOTAL_SAVED=$((TOTAL_SAVED + size_bytes))
        log_status "RUNNING TOTAL: $DUPLICATES_PROCESSED duplicates processed, $(format_size $TOTAL_SAVED) saved so far"
    }
    
    # Process duplicates based on auto mode or interactive mode
    if [ "$AUTO_MODE" = true ]; then
        # Automated mode - delete all duplicates
        if [ $duplicate_warnings -gt 0 ]; then
            log_status "Deleting all duplicates..."
        }
        
        for path in "${duplicates[@]}"; do
            process_duplicate "$path" true
        done
        
        # Log batch completion
        if [ ${#duplicates[@]} -gt 0 ]; then
            log_status "All duplicates have been processed."
            batch_size_human=$(format_size $((TOTAL_SAVED - previous_total)))
            log_status "This batch: $batch_size_human saved"
        }
    else
        # Interactive mode - ask for confirmation
        if [ ${#duplicates[@]} -gt 0 ]; then
            log_status "\nWould you like to delete the duplicate(s) and keep only the official version? (y/n): "
            read -r confirm
            
            if [ "$confirm" = "y" ]; then
                for path in "${duplicates[@]}"; do
                    # Skip if path doesn't exist
                    if [ ! -d "$path" ]; then
                        continue
                    }
                    
                    # Ask for confirmation for each path
                    log_status "CONFIRM: Delete $path? (y/n): "
                    read -r path_confirm
                    
                    if [ "$path_confirm" = "y" ]; then
                        process_duplicate "$path" false
                    else
                        log_status "Skipping $path"
                    fi
                done
            else
                log_status "Skipping all duplicates for this folder."
            }
        fi
    fi
    
    # Pause between folders in interactive mode
    if [ "$AUTO_MODE" = false ]; then
        log_status "\nPress Enter to continue to the next folder..."
        read -r
    fi
done

# Generate summary report
log_status "\n${BLUE}====== Cleanup Summary ======${NC}"
log_status "Total folders processed: $TOTAL_FOLDERS"
log_status "Total folders skipped: $SKIPPED_FOLDERS"
log_status "Total skipped due to size constraints: $SIZE_SKIPPED"
log_status "Total safety warnings: $SAFETY_WARNINGS"
log_status "Total duplicates processed: $DUPLICATES_PROCESSED"

if [ "$DRY_RUN" = true ]; then
    log_status "Potential space to be saved: $(format_size $TOTAL_SAVED)"
    log_status "No files were actually deleted (dry run mode)"
else
    log_status "Total space saved: $(format_size $TOTAL_SAVED)"
fi

log_status "${BLUE}===============================${NC}"
log_status "Cleanup session completed at $(date)"
log_status "Log saved to $LOG_FILE"

# Write summary report to file
cat > "$SUMMARY_REPORT" <<EOL
=== Media Duplicates Cleanup Summary ===
Date: $(date)
Report file: $REPORT_FILE
Filter: ${FILTER_PATTERN:-"None"}
Min size: $(format_size $MIN_SIZE)
Max size: $(format_size $MAX_SIZE)
Dry run: $DRY_RUN
Auto mode: $AUTO_MODE

Total folders processed: $TOTAL_FOLDERS
Total folders skipped: $SKIPPED_FOLDERS
Total skipped due to size constraints: $SIZE_SKIPPED
Total safety warnings: $SAFETY_WARNINGS
Total duplicates processed: $DUPLICATES_PROCESSED
Total space saved: $(format_size $TOTAL_SAVED)
EOL

echo "Cleanup session log saved to $LOG_FILE"
echo "Summary report saved to $SUMMARY_REPORT" 