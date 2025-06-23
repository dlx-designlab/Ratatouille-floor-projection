#!/usr/bin/env python3
"""
WiFi Server for Raspberry Pi
Continuously sends PIR sensor state via WiFi
"""

import socket
import json
import threading
import time
import RPi.GPIO as GPIO
from datetime import datetime

# GPIO Configuration
PIR_PIN = 24
GPIO.setmode(GPIO.BCM)
GPIO.setup(PIR_PIN, GPIO.IN)

# Network Configuration
HOST = '0.0.0.0'  # Listen on all interfaces
PORT = 5555       # Server port
BUFFER_SIZE = 1024
SEND_INTERVAL = 0.1  # Send state every 100ms

# Global variables
clients = []
pir_state = 0  # Current PIR state (0 or 1)
running = True
server_socket = None
motion_count = 0
last_state_change = 0

def get_ip_address():
    """Get the Raspberry Pi's IP address"""
    try:
        # Create temporary socket to get IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

def read_pir_state():
    """Read current PIR sensor state"""
    return GPIO.input(PIR_PIN)

def broadcast_to_clients(message):
    """Send message to all connected clients"""
    global clients
    disconnected_clients = []
    
    for client_socket, client_address in clients:
        try:
            client_socket.send(message.encode())
        except:
            disconnected_clients.append((client_socket, client_address))
            print(f"[DISCONNECTED] Client {client_address} disconnected")
    
    # Remove disconnected clients
    for client in disconnected_clients:
        if client in clients:
            clients.remove(client)
            client[0].close()

def handle_client(client_socket, client_address):
    """Handle client connection"""
    global clients
    
    print(f"[CONNECTED] New client: {client_address}")
    clients.append((client_socket, client_address))
    
    # Send welcome message
    welcome_msg = {
        "type": "welcome",
        "message": "Connected to PIR server",
        "timestamp": datetime.now().isoformat(),
        "pir_state": pir_state
    }
    try:
        client_socket.send((json.dumps(welcome_msg) + "\n").encode())
    except:
        pass

def monitor_sensor():
    """Thread that continuously reads and sends sensor state"""
    global pir_state, running, motion_count, last_state_change
    
    previous_state = 0
    send_counter = 0
    
    while running:
        # Read current PIR state
        current_state = read_pir_state()
        
        # Detect state changes
        if current_state != previous_state:
            if current_state == 1:  # Motion detected
                motion_count += 1
                print(f"[PIR] Motion detected! (Total: {motion_count})")
            else:
                print(f"[PIR] Motion ended")
            
            previous_state = current_state
            last_state_change = time.time()
        
        # Update global state
        pir_state = current_state
        
        # Create message with current state
        data = {
            "type": "pir_state",
            "timestamp": datetime.now().isoformat(),
            "state": current_state,  # 0 or 1
            "motion": bool(current_state),  # True or False
            "pin": PIR_PIN,
            "motion_count": motion_count,
            "time_since_change": time.time() - last_state_change if last_state_change > 0 else 0,
            "clients_connected": len(clients)
        }
        
        # Send to all clients
        if clients:  # Only if clients are connected
            message = json.dumps(data) + "\n"
            broadcast_to_clients(message)
            
            # Periodic log (every 10 transmissions)
            send_counter += 1
            if send_counter % 10 == 0:
                state_text = "MOTION" if current_state else "IDLE"
                print(f"[STATUS] PIR: {state_text} | Clients: {len(clients)} | Sent: {send_counter}")
        
        # Wait before next reading
        time.sleep(SEND_INTERVAL)

def start_server():
    """Start TCP server"""
    global server_socket, running
    
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    try:
        server_socket.bind((HOST, PORT))
        server_socket.listen(5)
        
        ip_address = get_ip_address()
        
        print("\n" + "="*60)
        print("   PIR WiFi SERVER - CONTINUOUS MODE")
        print("="*60)
        print(f"📡 IP Address: {ip_address}")
        print(f"🔌 Port: {PORT}")
        print(f"📍 PIR Sensor: GPIO {PIR_PIN}")
        print(f"📤 Send interval: {SEND_INTERVAL*1000}ms")
        print("-"*60)
        print(f"💻 Clients should connect to: {ip_address}:{PORT}")
        print("="*60 + "\n")
        
        # Display initial state
        initial_state = read_pir_state()
        print(f"[INIT] Initial PIR state: {'MOTION' if initial_state else 'IDLE'}")
        
        while running:
            try:
                # Accept connections
                server_socket.settimeout(1.0)
                try:
                    client_socket, client_address = server_socket.accept()
                    # Handle client in separate thread
                    client_thread = threading.Thread(
                        target=handle_client,
                        args=(client_socket, client_address)
                    )
                    client_thread.daemon = True
                    client_thread.start()
                except socket.timeout:
                    continue
                    
            except Exception as e:
                if running:
                    print(f"[ERROR] Server: {e}")
                    
    except Exception as e:
        print(f"[ERROR] Unable to start server: {e}")
    finally:
        if server_socket:
            server_socket.close()

def main():
    global running
    
    print("🚀 Starting PIR WiFi server (continuous mode)...")
    
    # Start monitoring thread
    monitor_thread = threading.Thread(target=monitor_sensor)
    monitor_thread.daemon = True
    monitor_thread.start()
    
    try:
        # Start server
        start_server()
        
    except KeyboardInterrupt:
        print("\n\n[INFO] Stopping server...")
    finally:
        running = False
        
        # Close all client connections
        for client_socket, _ in clients:
            client_socket.close()
        
        if server_socket:
            server_socket.close()
            
        GPIO.cleanup()
        print("[INFO] Server stopped cleanly")

if __name__ == "__main__":
    main()