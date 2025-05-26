#!/usr/bin/env python3
import sys
import os
import time
import json
import argparse
import multiprocessing
from queue import Empty
import signal
import random
from bloomfilter import BloomFilter
from crypto import secp256k1 as ice
from settings import ADDRESSES_FILE, DEFAULT_START_HEX, DEFAULT_STOP_HEX, FOUND_KEY_FILE

def privatekey_to_address(priv_bytes, is_compressed=True):
    priv_int = int.from_bytes(priv_bytes, 'big')
    return ice.privatekey_to_coinaddress(0, 0, is_compressed, priv_int)

import numpy as np
try:
    from tqdm import tqdm
except ImportError:
    # Define a proper fallback for tqdm if it's not installed
    class tqdm:
        def __init__(self, iterable=None, **kwargs):
            self.iterable = iterable
            self.total = kwargs.get('total', len(iterable) if iterable is not None else 0)
            self.n = 0
            self.desc = kwargs.get('desc', '')
            print(f"{self.desc} (0/{self.total})")
            
        def update(self, n=1):
            self.n += n
            print(f"\r{self.desc} ({self.n}/{self.total})", end="")
            
        def close(self):
            print("")
            
        def __iter__(self):
            if self.iterable:
                for obj in self.iterable:
                    yield obj

def calculate_batch_size(start, stop, is_random=False):
    """Calculate optimal batch size based on range size"""
    range_size = stop - start + 1
    
    if is_random:
        # For random mode, use larger batches for larger ranges
        if range_size > 2**64:  # Very large range
            return 1000000  # 1 million keys per batch
        elif range_size > 2**48:  # Large range
            return 500000   # 500k keys per batch
        elif range_size > 2**32:  # Medium range
            return 250000   # 250k keys per batch
        else:  # Small range
            return 100000   # 100k keys per batch
    else:
        # For sequential mode, use smaller batches
        if range_size > 2**64:
            return 500000
        elif range_size > 2**48:
            return 250000
        elif range_size > 2**32:
            return 100000
        else:
            return 50000

def signal_handler(signum, frame):
    """Handle termination signals gracefully"""
    if not hasattr(signal_handler, 'handled'):
        signal_handler.handled = True
        print("\n[EXIT] Terminating the program...")
        # Use os._exit to prevent multiple termination messages
        os._exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def load_target_addresses(addresses_file):
    try:
        if addresses_file.endswith(".bf"):
            with open(addresses_file, "rb") as fp:
                addresses = BloomFilter.load(fp)
        else:
            with open(addresses_file) as file:
                addresses = file.read().split()
    except FileNotFoundError:
        addresses = []

    return addresses

def search_worker(start, stop, queue, process_id, mode_name, target_addresses, args):
    """Function for each sub-process for searching."""
    try:
        tried = 0
        found_addresses = set()  # Track found addresses to prevent duplicates
        checked = set()
        remaining_targets = len(target_addresses)
        last_update = 0
        local_report = {"found": False, "found_keys": []}
        range_completed = False
        
        # Store original range
        original_start = start
        original_stop = stop
        
        # Initialize search state
        current_state = {
            'progress': 0,
            'success_rate': 0,
            'time_spent': 0,
            'range_size': stop - start + 1,
            'tries': 0,
            'found_count': 0,
            'error_rate': 0,
            'memory_usage': 0,
            'cpu_usage': 0,
            'key': hex(start)[2:].zfill(64)
        }
        
        # Calculate optimal chunk size based on range
        chunk_size = calculate_batch_size(start, stop, mode_name == "Random")
        
        current_position = start
        batch_start_time = time.time()
        last_progress_update = time.time()
        current_hex = hex(start)[2:].zfill(64)
        last_memory_cleanup = time.time()
        
        def generate_batch(start_pos, end_pos, is_random=False):
            """Generate a batch of numbers to check"""
            if is_random:
                try:
                    # Use dynamic sample size based on range
                    sample_size = min(chunk_size, end_pos - start_pos + 1)
                    
                    # Use a better random number generator with more entropy
                    rng = random.SystemRandom()
                    
                    # For random mode, use the entire range regardless of process boundaries
                    if is_random:
                        start_pos = original_start
                        end_pos = original_stop
                    
                    # Generate random numbers with more entropy
                    random_numbers = set()
                    while len(random_numbers) < sample_size:
                        # Generate random numbers in chunks to be more efficient
                        chunk = {rng.randint(start_pos, end_pos) for _ in range(min(10000, sample_size - len(random_numbers)))}
                        random_numbers.update(chunk)
                    
                    # Convert to list and shuffle for extra randomness
                    result = list(random_numbers)
                    rng.shuffle(result)
                    return result
                    
                except ValueError as e:
                    print(f"Process {process_id}: Error generating random sample: {str(e)}")
                    # Fallback to sequential if random sampling fails
                    return range(start_pos, end_pos + 1)
            else:
                # For sequential mode, use a range object instead of list
                return range(start_pos, end_pos + 1)
        
        while remaining_targets > 0 and not range_completed:
            try:
                # Calculate end of current chunk
                chunk_end = min(current_position + chunk_size - 1, stop)
                
                # Generate batch for current chunk
                current_batch = generate_batch(current_position, chunk_end, mode_name == "Random")
                
                # Process the current batch
                batch_size = len(current_batch)
                
                for priv_int in current_batch:
                    if priv_int in checked or priv_int < start or priv_int > stop:
                        continue
                        
                    checked.add(priv_int)
                    tried += 1
                    current_hex = hex(priv_int)[2:].zfill(64)
                    
                    # Send progress update more frequently
                    current_time = time.time()
                    if current_time - last_progress_update >= 2:
                        # Calculate per-process speed for the last interval
                        time_diff = current_time - last_progress_update
                        keys_diff = tried - last_update
                        per_process_speed = keys_diff / time_diff if time_diff > 0 else 0

                        queue.put(("PROGRESS", None, None, process_id, tried - last_update, mode_name, current_hex, per_process_speed))
                        last_update = tried
                        last_progress_update = current_time
                        
                        # Memory cleanup every 30 seconds
                        if current_time - last_memory_cleanup >= 30:
                            if len(checked) > 1000000:
                                checked.clear()
                            last_memory_cleanup = current_time
                    
                    try:
                        priv_bytes = bytes.fromhex(current_hex)
                        addresses_to_check = []
                        if args.both:
                            addresses_to_check.append(privatekey_to_address(priv_bytes, is_compressed=True))
                            addresses_to_check.append(privatekey_to_address(priv_bytes, is_compressed=False))
                        elif args.uc:
                            addresses_to_check.append(privatekey_to_address(priv_bytes, is_compressed=False))
                        else:
                            addresses_to_check.append(privatekey_to_address(priv_bytes, is_compressed=True))
                        
                        # Check if any of the generated addresses are in the target list and not already found
                        for address in addresses_to_check:
                            if address in target_addresses and address not in found_addresses:
                                # Send a special message to check if this key was already found
                                # Pass the found address in the message
                                queue.put(("CHECK", current_hex, address, process_id, tried, mode_name, current_hex))
                                # Once found, no need to check other address formats for this key
                                break
                    
                    except Exception as e:
                        print(f"Process {process_id}: Error with key {current_hex[:8]}...: {str(e)}")
                
                # Move to next chunk
                current_position = chunk_end + 1
                
                # Check if we've completed the range
                if current_position > stop:
                    range_completed = True
                    queue.put(("COMPLETE", None, None, process_id, tried, mode_name, current_hex))
                    # Signal that this process is available to help
                    queue.put(("HELP_REQUEST", None, None, process_id, 0, mode_name, current_hex))
                
            except Exception as e:
                print(f"Process {process_id}: Error in main loop: {str(e)}")
                print(f"Process {process_id}: Current position: {current_position}, Chunk end: {chunk_end}")
                print(f"Process {process_id}: Range: {start} -> {stop}")
                import traceback
                print(f"Process {process_id}: Traceback: {traceback.format_exc()}")
                break
                
    except Exception as e:
        print(f"Process {process_id}: Fatal error: {str(e)}")
        import traceback
        print(f"Process {process_id}: Traceback: {traceback.format_exc()}")

def load_progress(filename="scan_progress.json", start=None, stop=None):
    """Load the saved scanning progress"""
    try:
        if os.path.exists(filename):
            with open(filename, "r") as f:
                progress = json.load(f)
                
                # Check if the saved range matches our current range
                if "range_start" in progress and "range_stop" in progress and start is not None and stop is not None:
                    saved_start = int(progress["range_start"], 16)
                    saved_stop = int(progress["range_stop"], 16)
                    if saved_start != start or saved_stop != stop:
                        print("Saved progress is for a different range. Starting fresh.")
                        return {}
                
                # Only consider range completed if explicitly marked as such
                if progress.get("range_completed", False):
                    print("Previous range was completed. Starting fresh scan.")
                    return {}
                
                # Load process positions
                process_positions = {}
                for proc_id, data in progress.items():
                    if proc_id.isdigit():  # Only process numeric process IDs
                        try:
                            current_hex = data.get('current_hex', '0x0')
                            if isinstance(current_hex, str) and current_hex.startswith('0x'):
                                current_hex = current_hex[2:]
                            current_pos = int(current_hex, 16)
                            proc_id = int(proc_id)
                            process_positions[proc_id] = current_pos
                        except (ValueError, TypeError) as e:
                            print(f"Error loading progress for process {proc_id+1}: {str(e)}")
                            continue
                
                return process_positions
    except Exception as e:
        print(f"Error loading progress: {str(e)}")
    return {}

def save_current_progress(args, start, stop, total_keys_checked, total_found, start_time, completed_processes, num_processes, process_positions, process_cpu_usage, modes):
    """Save current progress to file (only for sequential and dance modes)"""
    if args.random and not args.dance:  # Don't save in pure random mode
        return True
        
    try:
        progress = {
            "range_start": hex(start),
            "range_stop": hex(stop),
            "timestamp": int(time.time()),
            "range_completed": False,
            "total_keys_checked": total_keys_checked,
            "total_found": total_found,
            "elapsed_time": time.time() - start_time,
            "mode": "dance" if args.dance else "sequential"
        }
        
        # Check if all processes are completed
        all_completed = all(proc_id in completed_processes for proc_id in range(num_processes))
        if all_completed:
            progress["range_completed"] = True
            print("\nAll processes completed their ranges. Marking scan as complete.")
        
        # Save current positions for each process
        for proc_id in range(num_processes):
            if proc_id in process_positions:
                current_pos = process_positions[proc_id]
                progress[str(proc_id)] = {
                    "current_hex": hex(current_pos),
                    "timestamp": int(time.time()),
                    "cpu_usage": process_cpu_usage.get(proc_id, 0),
                    "is_completed": proc_id in completed_processes,
                    "mode": modes[proc_id]  # Save the mode for each process
                }
        
        # Use temporary file for atomic write
        temp_file = "scan_progress.json.tmp"
        with open(temp_file, "w") as f:
            json.dump(progress, f, indent=2)
        if os.path.exists("scan_progress.json"):
            os.remove("scan_progress.json")
        os.rename(temp_file, "scan_progress.json")
        print(f"\nSaved progress at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        return True
    except Exception as e:
        print(f"Error saving progress: {str(e)}")
        return False

def manager(start, stop, num_processes, modes, target_addresses, args):
    manager_mp = multiprocessing.Manager()
    queue = manager_mp.Queue()
    processes = []
    
    # Initialize timing variables
    start_time = time.time()
    last_update_time = start_time
    last_save_time = start_time
    save_interval = 60  # Save every minute
    last_cpu_update = start_time
    cpu_update_interval = 5  # Update CPU stats every 5 seconds
    
    # Calculate total range size
    total_range = stop - start + 1
    is_large_range = total_range > 1000000000  # Over 1 billion keys
    
    # Track found addresses at the manager level to prevent duplicates
    found_addresses = set()
    total_found = 0
    total_keys_checked = 0
    original_ranges_completed = set()
    
    # Track CPU usage per process
    process_cpu_usage = {}
    
    # Calculate process ranges based on mode
    process_ranges = []
    if args.random and not args.dance:  # Only use random ranges in pure random mode
        # For random mode, divide the range into equal chunks for each CPU
        chunk_size = total_range // num_processes
        current_start = start
        for i in range(num_processes):
            process_end = current_start + chunk_size - 1
            if i == num_processes - 1:  # Last process gets to the end
                process_end = stop
            process_ranges.append((current_start, process_end))
            current_start = process_end + 1
    else:
        # For sequential and dance modes, use the original range division logic
        chunk_size = total_range // num_processes
        remainder = total_range % num_processes
        current_start = start
        for i in range(num_processes):
            extra = 1 if i < remainder else 0
            process_end = current_start + chunk_size + extra - 1
            if i == num_processes - 1:
                process_end = stop
            process_ranges.append((current_start, process_end))
            current_start = process_end + 1
    
    # Initialize process status
    process_status = {}
    process_positions = {}
    completed_processes = set()
    helping_processes = set() # New set to track processes that are helping
    
    def signal_handler(signum, frame):
        """Handle termination signals gracefully"""
        if not hasattr(signal_handler, 'handled'):
            signal_handler.handled = True
            print("\n[EXIT] Terminating the program...")
            
            # Print final summary
            print("\n=== Search Summary ===")
            print(f"Total keys checked: {total_keys_checked:,}")
            print(f"Total keys found: {total_found}")
            print(f"Time elapsed: {time.time() - start_time:.2f} seconds")
            print(f"Average speed: {total_keys_checked / (time.time() - start_time):.2f} keys/sec")
            print("=====================\n")
            
            # Save progress for sequential and dance modes
            if not args.random or args.dance:
                try:
                    progress = {
                        "range_start": hex(start),
                        "range_stop": hex(stop),
                        "timestamp": int(time.time()),
                        "range_completed": False,
                        "total_keys_checked": total_keys_checked,
                        "total_found": total_found,
                        "elapsed_time": time.time() - start_time,
                        "mode": "dance" if args.dance else "sequential"
                    }
                    
                    # Save current positions for each process
                    for proc_id in range(num_processes):
                        if proc_id in process_positions:
                            current_pos = process_positions[proc_id]
                            progress[str(proc_id)] = {
                                "current_hex": hex(current_pos),
                                "timestamp": int(time.time()),
                                "cpu_usage": process_cpu_usage.get(proc_id, 0),
                                "is_completed": proc_id in original_ranges_completed,
                                "mode": modes[proc_id]  # Save the mode for each process
                            }
                    
                    # Use temporary file for atomic write
                    temp_file = "scan_progress.json.tmp"
                    with open(temp_file, "w") as f:
                        json.dump(progress, f, indent=2)
                    if os.path.exists("scan_progress.json"):
                        os.remove("scan_progress.json")
                    os.rename(temp_file, "scan_progress.json")
                    print(f"Progress saved to scan_progress.json")
                except Exception as e:
                    print(f"Error saving progress: {str(e)}")
            
            # Clean up processes
            for p in processes:
                if p.is_alive():
                    p.terminate()
            
            # Wait for processes to finish
            for p in processes:
                p.join()
            
            # Use os._exit to prevent multiple termination messages
            os._exit(0)
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Load saved progress for sequential and dance modes
    if not args.random or args.dance:
        print("="*80)
        print("\nLoading saved progress...")
        saved_positions = load_progress(start=start, stop=stop)
        if saved_positions:
            for proc_id, current_pos in saved_positions.items():
                if proc_id < len(process_ranges):
                    process_start, process_stop = process_ranges[proc_id]
                    if current_pos >= process_stop:
                        completed_processes.add(proc_id)
                        print(f"Process {proc_id+1} was already completed (reached {hex(current_pos)})")
                    else:
                        process_positions[proc_id] = current_pos
                        print(f"Process {proc_id+1} continuing from {hex(current_pos)}")
            print("="*80)
            print("\nProgress loaded successfully")
            time.sleep(0.1)
        print("="*80)
    # Initialize process status for processes without saved positions
    for i in range(num_processes):
        if i not in process_positions:
            process_positions[i] = process_ranges[i][0]
        process_status[i] = {
            "status": "Running",
            "current": hex(process_positions[i])
        }
        print(f"Process {i+1} starting from {hex(process_positions[i])} (range: {hex(process_ranges[i][0])} -> {hex(process_ranges[i][1])})")
        print("="*80)
        time.sleep(0.1)

    def update_cpu_usage():
        """Update CPU usage for all processes"""
        try:
            import psutil
            for i, p in enumerate(processes):
                if p.is_alive():
                    try:
                        process = psutil.Process(p.pid)
                        # Get CPU percent with interval=0.1 to get a more accurate reading
                        cpu_percent = process.cpu_percent(interval=0.1)
                        # If we get 0.0, try one more time with a longer interval
                        if cpu_percent == 0.0:
                            cpu_percent = process.cpu_percent(interval=0.2)
                        process_cpu_usage[i] = cpu_percent
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        process_cpu_usage[i] = 0
        except ImportError:
            print("Warning: psutil module not found. CPU usage tracking disabled.")
        except Exception as e:
            print(f"Error updating CPU usage: {str(e)}")
    
    def save_found_key(key, address, process_id):
        """Save a found key to the file with organized formatting"""
        try:
            # Convert the key to integer for position
            position = int(key, 16)
            
            with open(FOUND_KEY_FILE, "a", encoding="utf-8") as f:
                # Add a separator line for better readability
                f.write("\n" + "="*80 + "\n")
                
                # Write the timestamp and process info
                f.write(f"Found at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Process ID: {process_id}\n")
                
                # Write the key information in a clear format
                f.write("\nKey Information:\n")
                f.write(f"Private Key (HEX): {key}\n")
                f.write(f"Private Key (DEC): {position}\n")
                f.write(f"Bitcoin Address: {address}\n")
                
                # Add position information
                f.write("\nPosition Details:\n")
                f.write(f"Decimal Position: {position:,}\n")
                f.write(f"Hex Position: 0x{position:x}\n")
                
                # Add a summary line
                f.write("\nSummary:\n")
                f.write(f"Found key #{position} in process {process_id}\n")
                
                # Add another separator line
                f.write("="*80 + "\n")
                
            return True
        except Exception as e:
            print(f"Error saving found key: {str(e)}")
            return False
    
    def print_progress():
        """Print current progress information"""
        try:
            current_time = time.time()
            elapsed = current_time - start_time
            keys_per_sec = total_keys_checked / elapsed if elapsed > 0 else 0
            
            # Count processes in different states
            # A process is considered 'running' for the summary if it's alive OR helping
            running_count = sum(1 for i, p in enumerate(processes) if p.is_alive() or i in helping_processes)
            # completed_count = len(original_ranges_completed)
            
            # Count helping processes and truly completed processes
            helping_count = len(helping_processes)
            truly_completed_count = 0
            for pid in range(num_processes):
                if pid in original_ranges_completed and pid not in helping_processes:
                    truly_completed_count += 1
            
            # Print overall progress
            print(f"\nProgress: {total_keys_checked:,} keys checked, "
                  f"{running_count} running, "
                  f"{truly_completed_count} completed, "
                  f"{helping_count} helping, "
                  f"{total_found} found")
            print(f"Speed: {keys_per_sec:.2f} keys/sec")
            
            # Only show progress bar for sequential mode
            if not args.random and not args.dance:
                # Calculate progress based on current positions vs total range
                total_progress = 0
                for i in range(num_processes):
                    if i in process_positions:
                        current_pos = process_positions[i]
                        process_start, process_stop = process_ranges[i]
                        process_range = process_stop - process_start + 1
                        if process_range > 0:
                            process_progress = min(1.0, (current_pos - process_start) / process_range)
                            total_progress += process_progress
                
                progress_percent = (total_progress / num_processes) * 100
                
                # Print progress bar based on actual progress
                progress_bar = f"Search Progress: {progress_percent:.1f}%|{'█' * int(progress_percent/2)}{' ' * (50-int(progress_percent/2))}| "
                progress_bar += f"{progress_percent:.1f}% complete"
                
                # Add time estimates
                if keys_per_sec > 0 and progress_percent > 0:
                    remaining_keys = total_keys_checked * (100 - progress_percent) / progress_percent
                    remaining_time = remaining_keys / keys_per_sec
                    progress_bar += f" [{int(elapsed/3600):02d}:{int((elapsed%3600)/60):02d}:{int(elapsed%60):02d}<"
                    progress_bar += f"{int(remaining_time/3600):02d}:{int((remaining_time%3600)/60):02d}:{int(remaining_time%60):02d}]"
                print(progress_bar)
            
            # Print process status with CPU usage
            print("\nProcess Status:")
            for pid, status in sorted(process_status.items()):
                status_text = status['status']
                if pid in helping_processes:
                    status_text = "Helping"
                elif pid in original_ranges_completed:
                    status_text = "Completed"
                cpu_usage = process_cpu_usage.get(pid, 0)
                # Format CPU usage to show at least one decimal place
                cpu_text = f"{cpu_usage:.1f}%" if cpu_usage > 0 else "0.0%"
                pos = status['current'] # Get current position from status
                # Include speed
                speed = status.get('keys_per_sec', 0)
                print(f"Process {pid+1}: {status_text} - Current: {pos} - CPU: {cpu_text} - Speed: {speed:,.0f} keys/s")
        except Exception as e:
            print(f"Error in print_progress: {str(e)}")
    
    def handle_help_request(proc_id, current_hex):
        """Handle a help request from a process"""
        # Only allow completed processes to help
        if proc_id not in original_ranges_completed:
            return False
        
        # Find a process that needs help
        for target_id in range(num_processes):
            if (target_id not in completed_processes and 
                target_id != proc_id):
                
                # Calculate the remaining range for the target process
                current_pos = process_positions[target_id]
                target_stop = process_ranges[target_id][1]
                remaining_range = target_stop - current_pos + 1
                
                if remaining_range > 1000000:  # Only help if there's significant work left
                    # Split the remaining range
                    help_stop = current_pos + (remaining_range // 2)
                    
                    # Send help response with new range in key and address fields
                    queue.put(("HELP_RESPONSE", current_pos, help_stop, target_id, 0, modes[proc_id] if proc_id < len(modes) else "Unknown", hex(current_pos)[2:].zfill(64)))
                    return True
        return False
    
    try:
        # Start all processes with their assigned ranges
        for i in range(num_processes):
            process_start, process_stop = process_ranges[i]
            current_pos = process_positions.get(i, process_start)
            
            p = multiprocessing.Process(
                target=search_worker,
                args=(current_pos, process_stop, queue, i, modes[i], target_addresses, args)
            )
            processes.append(p)
            p.start()
            print("="*80)
            print(f"Started process {i+1} with range {hex(current_pos)} -> {hex(process_stop)}")
            time.sleep(0.1)
        print("\nSearching... (Ctrl+C to stop)\n")
        
        # Monitor processes and handle results
        while any(p.is_alive() for p in processes):
            try:
                # Check for results with a timeout
                # Expecting 7 or 8 elements depending on message type
                result = queue.get(timeout=1)
                
                # Unpack based on message type
                if len(result) == 7:
                    msg_type, key, address, proc_id, tries, mode, current_hex = result
                    per_process_speed = 0 # Default speed for older message format
                elif len(result) == 8:
                    msg_type, key, address, proc_id, tries, mode, current_hex, per_process_speed = result
                else:
                    # Skip unknown message formats
                    print(f"Manager received unknown message format: {result}")
                    continue
                
                # Update statistics
                total_keys_checked += tries
                current_time = time.time()
                
                # Update process status and position
                process_status[proc_id]["current"] = current_hex
                # Store the per-process speed
                process_status[proc_id]["keys_per_sec"] = per_process_speed
                process_positions[proc_id] = int(current_hex, 16)
                
                # Check if process has completed its original range
                original_start, original_stop = process_ranges[proc_id]
                if process_positions[proc_id] >= original_stop and proc_id not in original_ranges_completed:
                    original_ranges_completed.add(proc_id)
                    print(f"Process {proc_id+1} completed its original range")
                
                if msg_type == "CHECK":  # Found a potential key
                    # Only process if we haven't found this address before
                    if address not in found_addresses:
                        found_addresses.add(address)
                        total_found += 1  # Increment found counter
                        print(f"\nFound key in Process {proc_id+1}:")
                        print(f"Address: {address}")
                        print(f"Private Key: {key[:8]}...{key[-8:]}")
                        print(f"Position: {current_hex}")
                        
                        # Save the found key to file
                        if save_found_key(key, address, proc_id+1):
                            print(f"Saved key to {FOUND_KEY_FILE}")
                elif msg_type == "COMPLETE":
                    process_status[proc_id]["status"] = "Completed"
                    print(f"Process {proc_id+1} completed its range (reached {current_hex})")
                    # Add to completed processes set
                    completed_processes.add(proc_id)
                elif msg_type == "HELP_REQUEST":
                    # Handle help request
                    if handle_help_request(proc_id, current_hex):
                        print(f"Process {proc_id+1} is now helping another process")
                elif msg_type == "HELP_RESPONSE":
                    # Received a new range to help with
                    # The worker handles updating its own range.
                    # We just need to mark this process as helping in the manager.
                    print(f"Manager: Process {proc_id+1} is starting to help another process.")
                    # Add the process that received the help response to the helping set
                    if proc_id not in helping_processes:
                        helping_processes.add(proc_id)
                        print(f"Manager: Added Process {proc_id+1} to helping_processes set. Current helping count: {len(helping_processes)}")
                elif msg_type == "PROGRESS":
                    if tries > 0:
                        # Update display every 2 seconds
                        if current_time - last_update_time >= 2:
                            print_progress()
                            last_update_time = current_time
                
                # Update CPU usage every 5 seconds
                if current_time - last_cpu_update >= cpu_update_interval:
                    update_cpu_usage()
                    last_cpu_update = current_time
                
                # Save progress every minute (only for sequential and dance modes)
                if (not args.random or args.dance) and current_time - last_save_time >= save_interval:
                    if save_current_progress(args, start, stop, total_keys_checked, total_found, start_time, completed_processes, num_processes, process_positions, process_cpu_usage, modes):
                        last_save_time = current_time
                    
            except Empty:
                continue
            except Exception as e:
                print(f"Error in manager loop: {str(e)}")
                continue
    
    except KeyboardInterrupt:
        print("\n[EXIT] Terminating the program...")
        if not args.random or args.dance:
            save_current_progress(args, start, stop, total_keys_checked, total_found, start_time, completed_processes, num_processes, process_positions, process_cpu_usage, modes)
    except Exception as e:
        print(f"\nError in manager: {str(e)}")
    finally:
        # Clean up processes
        for p in processes:
            if p.is_alive():
                p.terminate()
        
        # Wait for processes to finish
        for p in processes:
            p.join()

def main(args=None):
    parser = argparse.ArgumentParser(description="Bitcoin Private Key Search Tool")
    parser.add_argument('--mode', type=str, default='scan', choices=['scan'], help="Operating mode")
    parser.add_argument('--start', type=str, default=None, help="Start key in hexadecimal format")
    parser.add_argument('--stop', type=str, default=None, help="End key in hexadecimal format")
    parser.add_argument('--addresses-file', type=str, default=ADDRESSES_FILE, help="File with target addresses, one per line")
    parser.add_argument('--check-key', type=str, help="Check a specific private key in hex format")
    parser.add_argument('--random', action='store_true', help="Use random scanning mode instead of sequential")
    parser.add_argument('--dance', action='store_true', help="Alternate between sequential and random scanning")
    parser.add_argument('--uc', action='store_true', help="Use uncompressed address format instead of compressed")
    parser.add_argument('--both', action='store_true', help="Search for both compressed and uncompressed addresses")
    parser.add_argument('--cpu', type=int, help="Number of CPU cores to use (default: all available cores)")

    # Parse arguments
    if args is None:
        args = parser.parse_args()
    elif not isinstance(args, argparse.Namespace):
        args = parser.parse_args(args)

    # Special mode to check a specific private key
    if args.check_key:
        check_specific_key(args.check_key, args.addresses_file, args)
        return
    
    # Load target addresses from file
    target_addresses = load_target_addresses(args.addresses_file)
    if not target_addresses:
        print(f"Error: No addresses found in {args.addresses_file}. Please check the file content.")
        return
    
    # Use values from settings.py if not provided in args
    start_hex = args.start if args.start else DEFAULT_START_HEX
    stop_hex = args.stop if args.stop else DEFAULT_STOP_HEX
    
    print("\n" + "="*80)
    print("Bitcoin Private Key Search Tool")
    print("="*80)
    print(f"\nTarget addresses: {len(target_addresses)} addresses loaded from {args.addresses_file}")
    print(f"\nSearch range: {hex(int(start_hex, 16))} -> {hex(int(stop_hex, 16))}")
    
    # Display address formats being scanned
    address_formats = "compressed"
    if args.uc and not args.both:
        address_formats = "uncompressed"
    elif args.both:
        address_formats = "compressed and uncompressed"
    print(f"\nScanning for: {address_formats} addresses")
    
    start_int = int(start_hex, 16)
    stop_int = int(stop_hex, 16)
    
    print("="*80)
    # Pause for 1 seconds to show the information
    time.sleep(1)

    # Determine the number of processes to use
    if args.cpu is not None and args.cpu > 0:
        num_processes = args.cpu
        print(f"Using {num_processes} processes as specified by --cpu.")
        time.sleep(1)
    else:
        num_processes = os.cpu_count() or 1 # Default to all available cores, minimum 1
        print(f"Using {num_processes} processes (all available cores).")
        time.sleep(1)
        
    # Adjust process count for very small ranges
    range_size = stop_int - start_int + 1
    if range_size < num_processes * 1000: # If range is less than 1000 keys per process
        adjusted_num_processes = max(1, range_size // 100)
        if adjusted_num_processes < num_processes:
            print(f"Reducing number of processes to {adjusted_num_processes} for small range ({range_size:,} keys).")
            num_processes = adjusted_num_processes
        
    if args.dance:
        # For dance mode, alternate between sequential and random
        modes = []
        for i in range(num_processes):
            if i % 2 == 0:
                modes.append("Sequential")
            else:
                modes.append("Random")
        print("Using Dance mode (alternating Sequential and Random)")
    elif args.random:
        modes = ["Random"] * num_processes
        print("Using Random scanning mode")
    else:
        modes = ["Sequential"] * num_processes
        print("Using Sequential scanning mode")
    
    print(f"\nProcesses: {num_processes}")
    print("\nModes:", modes)
    print("\nInitializing search...")
    print("="*80)
    
    time.sleep(1)
    
    print("\nSearching... (Ctrl+C to stop)\n")
    
    manager(start_int, stop_int, num_processes, modes, target_addresses, args)

def check_specific_key(key_hex, addresses_file=ADDRESSES_FILE, args=None):
    """Check a specific private key and display information about it."""
    print("\n===== Private Key Check =====")
    
    try:
        # Clean up and format the key
        key_hex = key_hex.strip()
        if key_hex.startswith('0x'):
            key_hex = key_hex[2:]
            
        # Make sure the key is valid hexadecimal
        int(key_hex, 16)  # This will raise ValueError if not valid hex
        
        # Pad to 64 characters
        key_hex_padded = key_hex.zfill(64)
        
        # Display key info
        print(f"Input key: {key_hex}")
        print(f"Padded key: {key_hex_padded}")
        print(f"Decimal value: {int(key_hex, 16)}")
        
        # Convert to bytes for address generation
        try:
            key_bytes = bytes.fromhex(key_hex_padded)
            print(f"Key bytes length: {len(key_bytes)} bytes")
            
            # Always generate both compressed and uncompressed addresses for single key check
            compressed_address = privatekey_to_address(key_bytes, is_compressed=True)
            uncompressed_address = privatekey_to_address(key_bytes, is_compressed=False)
            
            print(f"Generated Compressed address: {compressed_address}")
            print(f"Generated Uncompressed address: {uncompressed_address}")
            
            # Add both to the list of addresses to check
            addresses_to_check = [compressed_address, uncompressed_address]
            
            print("Checking target list for both address formats...")
            
            # Check if in target list
            try:
                target_addresses = load_target_addresses(addresses_file)
                
                compressed_found = target_addresses and compressed_address in target_addresses
                uncompressed_found = target_addresses and uncompressed_address in target_addresses
                
                if compressed_found or uncompressed_found:
                    print(f"[OK] At least one address format for this key IS in your target list ({addresses_file})")
                    if compressed_found:
                        print(f"     - Compressed address {compressed_address} was found.")
                    if uncompressed_found:
                        print(f"     - Uncompressed address {uncompressed_address} was found.")
                else:
                    print(f"[X] Neither compressed ({compressed_address}) nor uncompressed ({uncompressed_address}) addresses for this key are in your target list ({addresses_file})")
                    
                # Suggest ways to add it to the list
                print("\nTo add these addresses to your search targets (add the relevant lines):")
                print(f"1. Edit {addresses_file}")
                print(f"2. Add the lines:")
                print(f"   {compressed_address}")
                print(f"   {uncompressed_address}")
                print("3. Save the file and run your search again")
                
            except Exception as e:
                print(f"Error checking target list: {str(e)}")
                
        except Exception as e:
            print(f"Error converting to bytes: {str(e)}")
            
    except ValueError as e:
        print(f"Invalid hexadecimal key: {str(e)}")
    except Exception as e:
        print(f"Error checking key: {str(e)}")
        
    print("============================\n")

if __name__ == "__main__":
    multiprocessing.freeze_support()
    # Call main without arguments to use sys.argv
    main()