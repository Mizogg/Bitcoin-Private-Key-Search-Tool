# Bitcoin Private Key Search Tool

A powerful GUI application for searching Bitcoin private keys and verifying addresses. This tool supports multiple search modes and provides real-time progress tracking.

## Features

- **Multiple Search Modes**:
  - Sequential Scanning
  - Random Scanning
  - Dance Scanning (combination of sequential and random)

- **Address Support**:
  - Searches for Bitcoin addresses starting with "1"
  - Supports both compressed and uncompressed formats
  - Bloom filter support for efficient address checking

- **User Interface**:
  - Modern, customizable theme system
  - Real-time progress tracking
  - Multiple tabs for different functions
  - Comprehensive help system

- **Key Features**:
  - Configurable key space ranges
  - CPU core optimization
  - Progress saving and resuming
  - Address file management
  - Private key verification

## Installation

1. Clone the repository:
```bash
git clone https://github.com/mizogg/bitcoin-private-key-search.git
cd bitcoin-private-key-search
```

2. Install the required dependencies:
```bash
pip install -r requirements.txt
```

3. Run the application:
```bash
# For GUI version:
python tkgui.py

# For CLI version:
python main.py [options]
```

## Usage

### GUI Version
#### Basic Search
1. Enter the key space range in hexadecimal format (e.g., 1:FFFFFFFF)
2. Select the search mode (Sequential, Random, or Dance)
3. Choose the address format (Compressed, Uncompressed, or Both)
4. Set the number of CPU cores to use
5. Click "Start Search"

#### Checking Keys
1. Go to the "Checking Keys and Files" tab
2. Enter a private key in hexadecimal format
3. Click "Check Key" to verify the key

#### Theme Customization
1. Go to File > Settings
2. Choose a preset theme or create a custom one
3. Adjust colors as needed
4. Click "Apply Theme" to see changes
5. Click "Save Theme" to persist your settings

### CLI Version
The command-line interface provides the same functionality as the GUI version but can be automated and run in scripts.

#### Command Line Options
```bash
python main.py [options]
```

Available options:
- `--start`: Start key in hexadecimal format (default: 1)
- `--stop`: End key in hexadecimal format (default: FFFFFFFF)
- `--addresses-file`: File with target addresses (default: btc.bf)
- `--check-key`: Check a specific private key
- `--random`: Use random scanning mode
- `--dance`: Alternate between sequential and random scanning
- `--uc`: Use uncompressed address format
- `--both`: Search for both compressed and uncompressed addresses
- `--cpu`: Number of CPU cores to use (default: all available cores)

#### CLI Examples

1. Sequential scan with custom range:
```bash
python main.py --start 1 --stop 1000000
```

2. Random scan with custom range:
```bash
python main.py --random --start 1 --stop 1000000
```

3. Check a specific private key:
```bash
python main.py --check-key 1234567890abcdef
```

4. Use a different addresses file:
```bash
python main.py --addresses-file my_addresses.bf
```

5. Sequential scan for uncompressed addresses:
```bash
python main.py --uc
```

6. Sequential scan for both compressed and uncompressed addresses:
```bash
python main.py --both
```

7. Sequential scan using a specific number of CPU cores (e.g., 4):
```bash
python main.py --cpu 4
```

8. Random scan for both address types using a specific number of cores:
```bash
python main.py --random --both --cpu 8
```

## File Formats

### Address Files
- **Text Files (.txt)**:
  - One address per line
  - Plain text format
  - Addresses must start with "1"

- **Bloom Filter Files (.bf)**:
  - More efficient for large address lists
  - Reduced memory usage
  - Faster address checking

## Notes

- The program only searches for and verifies Bitcoin addresses starting with "1"
- Both compressed and uncompressed address formats are supported
- Progress is automatically saved and can be resumed
- Found keys are saved to `found_keys.txt`
- Progress is saved to `scan_progress.json` and can be resumed

## Requirements

- Python 3.7 or higher
- See `requirements.txt` for full list of dependencies

## License

© 2025 Mizogg. All rights reserved.

## Support

For support and updates:
- Visit: https://mizogg.co.uk
- Telegram: https://t.me/TeamHunter_GUI

## Credits

Made by Team Mizogg
© mizogg.com 2018 - 2025
