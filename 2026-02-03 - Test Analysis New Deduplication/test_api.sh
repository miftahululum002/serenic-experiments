#!/bin/bash

# EMR Integration API Test Script for Deduplication Analysis
# This script tests the endpoints with djamil and rsabhk data

# Configuration
API_URL="https:/api.staging.serenic.ai/integrations/v2"
API_KEY_DJAMIL='sLPX9skYVwLn0Ey4Syb0qhdyHY0Ul9po' # Staging Dev Burner
API_KEY_RSABHK='DfMebVra5Ppoz82QM09YDhRDNRFJ4qX1' # Staging Dev Burner
FORCE_IPV6=false  # Set to true to force IPv6 resolution

# Base directory
BASE_DIR="/Users/miftahululum002/projects/serenic/experiments/2026-02-03 - Test Analysis New Deduplication"
# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to make API calls
call_endpoint() {
    local endpoint=$1
    local json_file=$2
    local description=$3
    local api_key=$4

    echo -e "${BLUE}Testing: ${description}${NC}"
    echo "Endpoint: ${API_URL}${endpoint}"
    echo "Using data from: ${json_file}"
    
    local curl_args=()
    [[ "$FORCE_IPV6" == "true" ]] && curl_args+=("-6")
    
    response=$(curl "${curl_args[@]}" -s -w "\n%{http_code}" -X POST \
        -H "Content-Type: application/json" \
        -d @"${json_file}" \
        -H "apiKey: $api_key" \
        "${API_URL}${endpoint}")
    
    # Extract status code (last line) and response body (everything else)
    status_code=$(echo "$response" | tail -n1)
    
    echo "Status: ${status_code}"
    echo "Response:"
    echo "${response}" | jq '.' 2>/dev/null || echo "${response%$'\n'*}"
    
    if [[ $status_code == *"200"* ]]; then
        echo -e "${GREEN}✓ Test passed${NC}"
    else
        echo -e "${RED}✗ Test failed${NC}"
    fi
    echo "----------------------------------------"
}

# Function to display menu and get selection
show_menu() {
    local title=$1
    shift
    local options=("$@")
    
    echo -e "${YELLOW}${title}${NC}"
    for i in "${!options[@]}"; do
        echo "  $((i+1)). ${options[i]}"
    done
    echo -n "Select option (1-${#options[@]}): "
}

# Function to get user selection
get_selection() {
    local max=$1
    local selection
    read selection
    while [[ ! "$selection" =~ ^[1-$max]$ ]]; do
        echo -e "${RED}Invalid selection. Please enter a number between 1 and $max${NC}"
        read selection
    done
    echo $((selection-1))
}

# Function to get yes/no
get_yes_no() {
    local prompt=$1
    local response
    while true; do
        echo -n "$prompt (y/n): "
        read response
        case $response in
            [Yy]* ) return 0;;
            [Nn]* ) return 1;;
            * ) echo "Please answer yes or no.";;
        esac
    done
}

# Main script
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}EMR Integration API Test Script${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Select folder (djamil or rsabhk)
FOLDER_OPTIONS=("1_djamil" "2_rsabhk")
show_menu "Select data folder:" "${FOLDER_OPTIONS[@]}"
FOLDER_IDX=$(get_selection ${#FOLDER_OPTIONS[@]})
FOLDER_NAME="${FOLDER_OPTIONS[$FOLDER_IDX]}"
API_KEY=$([ "$FOLDER_NAME" == "1_djamil" ] && echo "$API_KEY_DJAMIL" || echo "$API_KEY_RSABHK")

echo -e "${GREEN}Selected: ${FOLDER_NAME}${NC}"
echo ""

# Select test scenario
SCENARIO_OPTIONS=("1_from_empty" "2_existing" "3_large_number")
show_menu "Select test scenario:" "${SCENARIO_OPTIONS[@]}"
SCENARIO_IDX=$(get_selection ${#SCENARIO_OPTIONS[@]})
SCENARIO_NAME="${SCENARIO_OPTIONS[$SCENARIO_IDX]}"

echo -e "${GREEN}Selected: ${SCENARIO_NAME}${NC}"
echo ""

# Build file paths
FOLDER_PATH="${BASE_DIR}/${FOLDER_NAME}/${SCENARIO_NAME}"
PREREQUISITES_PATH="${BASE_DIR}/${FOLDER_NAME}/prerequisites.json"

# Check if prerequisites file exists
if [ ! -f "$PREREQUISITES_PATH" ]; then
    echo -e "${YELLOW}Warning: prerequisites.json not found at ${PREREQUISITES_PATH}${NC}"
    echo -e "${YELLOW}Prerequisites endpoint will be skipped${NC}"
    SKIP_PREREQUISITES=true
else
    SKIP_PREREQUISITES=false
fi

# Determine available files based on scenario
if [ "$SCENARIO_NAME" == "1_from_empty" ]; then
    NEW_ENCOUNTERS_FILE="${FOLDER_PATH}/new_encounters.json"
    UPDATE_ENCOUNTERS_FILE="${FOLDER_PATH}/update_encounters.json"
elif [ "$SCENARIO_NAME" == "2_existing" ]; then
    NEW_ENCOUNTERS_FILE="${FOLDER_PATH}/new_encounters.json"
    UPDATE_ENCOUNTERS_FILE="${FOLDER_PATH}/update_encounters.json"
    UPDATE_ENCOUNTERS_FILTERED_FILE="${FOLDER_PATH}/update_encounters_filtered.json"
elif [ "$SCENARIO_NAME" == "3_large_number" ]; then
    NEW_ENCOUNTERS_1_FILE="${FOLDER_PATH}/1_new_encounters.json"
    UPDATE_ENCOUNTERS_1_FILE="${FOLDER_PATH}/1_update_encounters.json"
    NEW_ENCOUNTERS_2_FILE="${FOLDER_PATH}/2_new_encounters.json"
    UPDATE_ENCOUNTERS_2_FILE="${FOLDER_PATH}/2_update_encounters.json"
fi

# Arrays to store selected endpoints
SELECTED_ENDPOINTS=()

# Collect user selections
echo -e "${BLUE}Select endpoints to test:${NC}"
echo ""

# Prerequisites endpoint
if [ "$SKIP_PREREQUISITES" == false ]; then
    if get_yes_no "Send prerequisites?"; then
        SELECTED_ENDPOINTS+=("prerequisites|${PREREQUISITES_PATH}|Prerequisites")
    fi
fi

# New encounters endpoint
if [ "$SCENARIO_NAME" == "3_large_number" ]; then
    echo -e "${YELLOW}Large number scenario detected${NC}"
    if get_yes_no "Send first part new encounters (1_new_encounters.json)?"; then
        SELECTED_ENDPOINTS+=("new|${NEW_ENCOUNTERS_1_FILE}|New Encounters (Part 1)")
    fi
    if get_yes_no "Send second part new encounters (2_new_encounters.json)?"; then
        SELECTED_ENDPOINTS+=("new|${NEW_ENCOUNTERS_2_FILE}|New Encounters (Part 2)")
    fi
else
    if get_yes_no "Send new encounters?"; then
        SELECTED_ENDPOINTS+=("new|${NEW_ENCOUNTERS_FILE}|New Encounters")
    fi
fi

# Update encounters endpoint
if [ "$SCENARIO_NAME" == "2_existing" ]; then
    echo ""
    UPDATE_OPTIONS=("update_encounters.json" "update_encounters_filtered.json")
    show_menu "Select update encounters file:" "${UPDATE_OPTIONS[@]}"
    UPDATE_IDX=$(get_selection ${#UPDATE_OPTIONS[@]})
    UPDATE_FILE="${FOLDER_PATH}/${UPDATE_OPTIONS[$UPDATE_IDX]}"
    
    if get_yes_no "Send update encounters?"; then
        SELECTED_ENDPOINTS+=("update|${UPDATE_FILE}|Update Encounters (${UPDATE_OPTIONS[$UPDATE_IDX]})")
    fi
elif [ "$SCENARIO_NAME" == "3_large_number" ]; then
    if get_yes_no "Send first part update encounters (1_update_encounters.json)?"; then
        SELECTED_ENDPOINTS+=("update|${UPDATE_ENCOUNTERS_1_FILE}|Update Encounters (Part 1)")
    fi
    if get_yes_no "Send second part update encounters (2_update_encounters.json)?"; then
        SELECTED_ENDPOINTS+=("update|${UPDATE_ENCOUNTERS_2_FILE}|Update Encounters (Part 2)")
    fi
else
    if get_yes_no "Send update encounters?"; then
        SELECTED_ENDPOINTS+=("update|${UPDATE_ENCOUNTERS_FILE}|Update Encounters")
    fi
fi

# Display summary and ask for confirmation
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Test Summary${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "Folder: ${GREEN}${FOLDER_NAME}${NC}"
echo -e "Scenario: ${GREEN}${SCENARIO_NAME}${NC}"
echo -e "API URL: ${YELLOW}${API_URL}${NC}"
if [ "$FORCE_IPV6" == "true" ]; then
    echo -e "IPv6: ${YELLOW}Enabled${NC}"
fi
echo ""
echo -e "${BLUE}Selected endpoints:${NC}"
if [ ${#SELECTED_ENDPOINTS[@]} -eq 0 ]; then
    echo -e "${YELLOW}  (none selected)${NC}"
else
    for endpoint in "${SELECTED_ENDPOINTS[@]}"; do
        IFS='|' read -r type file desc <<< "$endpoint"
        echo -e "  • ${desc}"
        echo -e "    File: ${file}"
    done
fi
echo ""
echo -e "${BLUE}========================================${NC}"

# Confirmation
if [ ${#SELECTED_ENDPOINTS[@]} -eq 0 ]; then
    echo -e "${YELLOW}No endpoints selected. Exiting.${NC}"
    exit 0
fi

if ! get_yes_no "Proceed with API calls?"; then
    echo -e "${YELLOW}Cancelled. Exiting.${NC}"
    exit 0
fi

echo ""
echo -e "${GREEN}Starting API calls...${NC}"
echo ""

# Health Check endpoint (always runs)
echo -e "${BLUE}Testing: Health Check${NC}"
echo "Endpoint: ${API_URL}/health_check"
curl_args=()
[[ "$FORCE_IPV6" == "true" ]] && curl_args+=("-6")
health_response=$(curl "${curl_args[@]}" -s -X GET \
    -H "apiKey: $API_KEY" \
    "${API_URL}/health_check")

echo "Response:"
echo "${health_response}"

if [[ $health_response == *"healthy"* ]]; then
    echo -e "${GREEN}✓ Health check passed${NC}"
else
    echo -e "${RED}✗ Health check failed${NC}"
fi
echo "----------------------------------------"
echo ""

# Execute selected endpoints
for endpoint in "${SELECTED_ENDPOINTS[@]}"; do
    IFS='|' read -r type file desc <<< "$endpoint"
    case $type in
        "prerequisites")
            call_endpoint "/prerequisites" "$file" "$desc" "$API_KEY"
            ;;
        "new")
            call_endpoint "/encounters/new" "$file" "$desc" "$API_KEY"
            ;;
        "update")
            call_endpoint "/encounters/update" "$file" "$desc" "$API_KEY"
            ;;
    esac
done

echo ""
echo -e "${GREEN}All tests completed.${NC}"
