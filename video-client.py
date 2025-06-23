#!/usr/bin/env python3
"""
Video Player contrôlé par détecteur PIR via WiFi
Remplace la barre d'espace par la détection de mouvement
"""

import pygame, time, logging, asyncio, vlc, os
import socket, json, threading
from datetime import datetime

# Config setup
SETTINGS = {
    'res': (2560, 1440),       # Adjusted for Raspberry Pi 2
    'no_input_secs': 8,       # wait for no motion detection
    'motion_debounce_ms': 200, # debounce for motion detection
    'fps': 60,                # smooth input
    'fade_ms': 500,          # 1 sec fades
    'vid_path': 'video.mp4',   # single video
    'bg_color': (0,0,0),      # black background
    # WiFi settings
    'raspberry_pi_ip': None,  # Will be set via input or auto-detect
    'port': 5555,
    'buffer_size': 1024,
}

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger()

class PIRClient:
    """Handles WiFi connection to PIR sensor"""
    def __init__(self):
        self.socket = None
        self.connected = False
        self.motion_detected = False
        self.last_motion_time = 0   
        self.running = True
        self.receive_thread = None
        
    def find_raspberry_pi(self):
        """Auto-detect Raspberry Pi on network"""
        log.info("🔍 Searching for PIR server on network...")
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            
            network_prefix = '.'.join(local_ip.split('.')[:-1]) + '.'
            log.info(f"Scanning network {network_prefix}0/24")
            
        except:
            log.error("Cannot determine local network")
            return None
        
        for i in range(1, 255):
            ip = network_prefix + str(i)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.05)
            
            try:
                result = sock.connect_ex((ip, SETTINGS['port']))
                if result == 0:
                    log.info(f"✅ Found PIR server at: {ip}")
                    return ip
            except:
                pass
            finally:
                sock.close()
        
        log.error("No PIR server found on network")
        return None
    
    def connect(self, ip=None):
        """Connect to PIR server"""
        if not ip:
            ip = self.find_raspberry_pi()
            if not ip:
                return False
                
        try:
            log.info(f"🔄 Connecting to PIR server at {ip}:{SETTINGS['port']}...")
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(5)
            self.socket.connect((ip, SETTINGS['port']))
            self.connected = True
            log.info("✅ Connected to PIR server!")
            
            # Start receive thread
            self.receive_thread = threading.Thread(target=self._receive_data)
            self.receive_thread.daemon = True
            self.receive_thread.start()
            
            return True
            
        except Exception as e:
            log.error(f"Connection failed: {e}")
            return False
    
    def _receive_data(self):
        """Receive data from PIR server"""
        buffer = ""
        previous_state = 0
        
        while self.running and self.connected:
            try:
                self.socket.settimeout(1.0)
                data = self.socket.recv(SETTINGS['buffer_size']).decode('utf-8')
                
                if not data:
                    log.warning("Connection closed by server")
                    self.connected = False
                    break
                
                buffer += data
                
                # Process complete messages
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    if line.strip():
                        try:
                            message = json.loads(line)
                            
                            # Handle continuous PIR state
                            if message.get('type') == 'pir_state':
                                current_state = message.get('state', 0)
                                
                                # Detect rising edge (0 -> 1)
                                if current_state == 1 and previous_state == 0:
                                    self.motion_detected = True
                                    self.last_motion_time = time.time()
                                    log.info(f"🚨 Motion detected! (Count: {message.get('motion_count', 0)})")
                                
                                previous_state = current_state
                                
                            # Handle welcome message
                            elif message.get('type') == 'welcome':
                                log.info(f"Connected: {message.get('message')}")
                                previous_state = message.get('pir_state', 0)
                                
                        except json.JSONDecodeError:
                            pass
                            
            except socket.timeout:
                continue
            except Exception as e:
                log.error(f"Receive error: {e}")
                self.connected = False
                break
    
    def get_motion(self):
        """Check if motion was detected"""
        if self.motion_detected:
            # Apply debounce
            if time.time() - self.last_motion_time > SETTINGS['motion_debounce_ms'] / 1000:
                self.motion_detected = False
                return True
        return False
    
    def close(self):
        """Close connection"""
        self.running = False
        if self.socket:
            self.socket.close()
        self.connected = False

class VidPlayer:
    def __init__(self, pir_client):
        self.pir_client = pir_client
        
        # Start pygame
        pygame.init()
        self.win = pygame.display.set_mode(SETTINGS['res'], pygame.FULLSCREEN)
        pygame.mouse.set_visible(False)  # hide cursor
        self.clock = pygame.time.Clock()
        self.active = True  # app running
        self.is_fading = False  # track fade state

        # VLC setup with adjust filter
        self.vlc = vlc.Instance('--no-osd --fullscreen --no-video-title-show --vout-filter=adjust')
        self.player = self.vlc.media_player_new()
        
        # Hook VLC to pygame
        if os.name == 'posix':
            win_info = pygame.display.get_wm_info()
            if 'window' in win_info:
                self.player.set_xwindow(win_info['window'])
                log.info('VLC linked to pygame on Linux')
        elif os.name == 'nt':
            win_info = pygame.display.get_wm_info()
            if 'window' in win_info:
                self.player.set_hwnd(win_info['window'])
                log.info('VLC linked to pygame on Windows')

        # Enable video adjustments
        self.player.video_set_adjust_int(vlc.VideoAdjustOption.Enable, 1)
        self.player.video_set_adjust_float(vlc.VideoAdjustOption.Contrast, 1.0)
        log.info('Video adjustments ready')

        # Clear screen
        self.win.fill(SETTINGS['bg_color'])
        pygame.display.flip()
        self.vid_done = False  # track video end
        
        # Watch for video end
        self.player.event_manager().event_attach(
            vlc.EventType.MediaPlayerEndReached, self._vid_end)

    def _vid_end(self, event):
        # Video hits end
        if event.type == vlc.EventType.MediaPlayerEndReached:
            log.info('Video done')
            self.vid_done = True

    def start_video(self):
        # Start the video
        path = SETTINGS['vid_path']
        if not os.path.exists(path):
            log.error(f"Video not found: {path}")
            return
        log.info(f'Playing {path}')
        self.win.fill(SETTINGS['bg_color'])
        pygame.display.flip()
        media = self.vlc.media_new(path)
        self.player.set_media(media)
        self.player.set_fullscreen(True)
        self.player.video_set_adjust_float(vlc.VideoAdjustOption.Contrast, 1.0)
        self.vid_done = False
        self.player.play()
        time.sleep(0.2)  # let VLC start

    def fade_to_black(self):
        # Fade video to black via contrast
        log.info('Fading video to black')
        self.is_fading = True
        steps = int(SETTINGS['fade_ms'] / (1000 / SETTINGS['fps']))
        for step in range(steps):
            contrast = 1.0 - (step / (steps - 1))
            self.player.video_set_adjust_float(vlc.VideoAdjustOption.Contrast, contrast)
            log.info(f'Contrast: {contrast:.2f}')
            self.clock.tick(SETTINGS['fps'])
        self.player.video_set_adjust_float(vlc.VideoAdjustOption.Contrast, 0)
        self.is_fading = False

    def fade_from_black(self):
        # Fade video back via contrast
        log.info('Fading video from black')
        self.is_fading = True
        steps = int(SETTINGS['fade_ms'] / (1000 / SETTINGS['fps']))
        for step in range(steps):
            contrast = step / (steps - 1)
            self.player.video_set_adjust_float(vlc.VideoAdjustOption.Contrast, contrast)
            log.info(f'Contrast: {contrast:.2f}')
            self.clock.tick(SETTINGS['fps'])
        self.player.video_set_adjust_float(vlc.VideoAdjustOption.Contrast, 1.0)
        self.is_fading = False

    def check_motion_or_input(self):
        """Check for motion detection or keyboard input"""
        while self.active:
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    self.active = False
                    return False
                if e.type == pygame.KEYDOWN:
                    if e.key == pygame.K_ESCAPE:
                        log.info('Escape pressed')
                        self.active = False
                        return False
                    elif e.key == pygame.K_SPACE:
                        log.info('Manual trigger (spacebar)')
                        return True
            
            # Check PIR motion detection
            if self.pir_client.get_motion() and not self.is_fading:
                log.info('Motion detected by PIR sensor!')
                return True
            
            # Handle video end
            if self.vid_done:
                log.info('Video ended, restart')
                self.start_video()
                return False
                
            self.clock.tick(SETTINGS['fps'])
        return False

    def wait_no_motion(self, secs):
        """Wait for no motion detection"""
        start = None
        last_motion_check = 0
        
        while self.active:
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    self.active = False
                    return False
                if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                    self.active = False
                    return False
            
            # Check for motion
            current_time = time.time()
            if current_time - last_motion_check > 0.1:  # Check every 100ms
                # Motion is detected if we received a motion event recently
                if self.pir_client.motion_detected or (current_time - self.pir_client.last_motion_time < 0.5):
                    start = None
                    log.info('Motion active, reset timer')
                    self.pir_client.motion_detected = False  # Clear flag
                else:
                    if start is None:
                        start = time.time()
                    elapsed = time.time() - start
                    if elapsed >= secs:
                        log.info(f'No motion for {secs}s')
                        return True
                    elif elapsed > 1:  # Log progress after 1 second
                        remaining = secs - elapsed
                        log.info(f'No motion timer: {remaining:.1f}s remaining')
                last_motion_check = current_time
                
            self.clock.tick(SETTINGS['fps'])
        return False

    def shutdown(self):
        # Clean up
        log.info('Shutting down')
        if self.player:
            self.player.stop()
            self.player.release()
        self.vlc.release()
        pygame.mouse.set_visible(True)
        pygame.quit()

async def run():
    # Main loop
    print('='*60)
    print('   VIDEO PLAYER WITH PIR MOTION DETECTION')
    print('='*60)
    
    # Setup PIR client
    pir_client = PIRClient()
    
    # Get Raspberry Pi IP
    if len(os.sys.argv) > 1:
        raspberry_ip = os.sys.argv[1]
    else:
        print("\n1. Auto-detect PIR server")
        print("2. Enter IP manually")
        choice = input("\nYour choice (1 or 2): ").strip()
        
        if choice == "1":
            raspberry_ip = None
        else:
            raspberry_ip = input("Enter Raspberry Pi IP: ").strip()
    
    # Connect to PIR server
    if not pir_client.connect(raspberry_ip):
        print("\n❌ Failed to connect to PIR server")
        print("Make sure the server is running on the Raspberry Pi")
        return
    
    player = VidPlayer(pir_client)
    
    try:
        # Start video once
        log.info('Starting video')
        player.start_video()

        while player.active:
            # Check for motion or manual trigger
            if player.check_motion_or_input():
                log.info('Motion detected -> fade video')
                player.fade_to_black()

                # Wait for no motion
                log.info('Waiting for no motion')
                if not player.wait_no_motion(SETTINGS['no_input_secs']):
                    break

                # Fade back without restarting
                log.info('No motion -> fade back')
                player.fade_from_black()

            # Handle video end or quit
            if not player.active:
                break
            await asyncio.sleep(0.05)

    except KeyboardInterrupt:
        log.info('User quit')
    finally:
        pir_client.close()
        player.shutdown()

if __name__ == '__main__':
    asyncio.run(run())