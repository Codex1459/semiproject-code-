import socket
import time
import threading
from gpiozero import Servo, RGBLED, Buzzer
from gpiozero.pins.pigpio import PiGPIOFactory
import RPi.GPIO as GPIO

# ---------------------------------------------
#  ANSI Terminal Colors
# ---------------------------------------------
R  = "\033[91m";  G  = "\033[92m";  Y  = "\033[93m"
B  = "\033[94m";  M  = "\033[95m";  C  = "\033[96m"
W  = "\033[97m";  DIM = "\033[2m";  RST = "\033[0m"
BOLD = "\033[1m"

def log(tag, msg, color=W):
    ts = time.strftime("%H:%M:%S")
    print(f"{DIM}[{ts}]{RST} {color}{BOLD}[{tag}]{RST} {msg}")

BANNER = f"""
{M}+------------------------------------------------------+
|  {W}{BOLD} ██████╗  █████╗ ███████╗██████╗ ██╗  ██████╗{M}       |
|  {W}{BOLD} ██╔══██╗██╔══██╗██╔════╝██╔══██╗██║ ██╔═══██╗{M}      |
|  {W}{BOLD} ██████╔╝███████║███████╗██████╔╝██║ ██║   ██║{M}      |
|  {W}{BOLD} ██╔══██╗██╔══██║╚════██║██╔═══╝ ██║ ██║   ██║{M}      |
|  {W}{BOLD} ██║  ██║██║  ██║███████║██║     ██║ ╚██████╔╝{M}      |
|  {W}{BOLD} ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝     ╚═╝  ╚═════╝ {M}      |
|                                                      |
|  {B}{BOLD}  Hardware Controller  --  Raspberry Pi Server  {M}     |
|  {DIM}  PWM Servo | RGB LED | Pulse Ultrasonic | Buzz {M}     |
+------------------------------------------------------+{RST}
"""

# --- Config ---
SERVO_PIN  = 18
LED_R, LED_G, LED_B = 22, 27, 25
TRIG_PIN, ECHO_PIN = 23, 24
BUZZER_PIN = 17

SERVO_OPEN   = -1.0  # 0 deg
SERVO_CLOSED = 0.0   # 90 deg

class HardwareController:
    def __init__(self):
        log("HW", "Initializing components...", C)
        try:
            factory = PiGPIOFactory()
            self.servo = Servo(SERVO_PIN, pin_factory=factory)
            self.rgb   = RGBLED(red=LED_R, green=LED_G, blue=LED_B)
            self.buzz  = Buzzer(BUZZER_PIN)
        except Exception as e:
            log("HW", f"Init Error: {e}", R)
            self.servo = self.rgb = self.buzz = None

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(TRIG_PIN, GPIO.OUT)
        GPIO.setup(ECHO_PIN, GPIO.IN)
        self.current_servo_val = SERVO_CLOSED
        if self.servo: self.servo.value = None

    def get_distance(self):
        try:
            GPIO.output(TRIG_PIN, True); time.sleep(0.00001); GPIO.output(TRIG_PIN, False)
            start, stop = time.time(), time.time()
            to = time.time() + 0.1
            while GPIO.input(ECHO_PIN) == 0:
                start = time.time()
                if start > to: return -1
            while GPIO.input(ECHO_PIN) == 1:
                stop = time.time()
                if stop > to: return -1
            return (stop - start) * 17150
        except: return -1

    def set_led(self, r, g, b):
        if self.rgb: self.rgb.color = (r, g, b)

    def play_buzzer(self, duration):
        if self.buzz: self.buzz.on(); time.sleep(duration); self.buzz.off()

    def set_servo_smooth(self, target_val, detach=True):
        target_val = max(-1.0, min(1.0, target_val))
        if not self.servo: return
        self.servo.value = self.current_servo_val
        diff = target_val - self.current_servo_val
        if diff == 0:
            if detach: self.servo.value = None
            return
        steps = max(1, int(abs(diff) / 0.01))
        sv = diff / steps
        for _ in range(steps):
            self.current_servo_val = max(-1.0, min(1.0, self.current_servo_val + sv))
            self.servo.value = self.current_servo_val
            time.sleep(0.02)
        self.servo.value = target_val
        if detach: self.servo.value = None

    def set_servo_smooth_timed(self, target_val, duration):
        """
        Perfectly smooth time-based servo glide.
        Uses real-time calculation to prevent jerks from thread jitter.
        """
        if not self.servo:
            time.sleep(duration)
            return
            
        target_val = max(-1.0, min(1.0, target_val))
        start_val = self.current_servo_val
        diff = target_val - start_val
        start_time = time.time()
        
        # Engage servo
        self.servo.value = start_val
        
        while True:
            elapsed = time.time() - start_time
            if elapsed >= duration: break
            
            # Calculate exact position based on current time
            v = start_val + diff * (elapsed / duration)
            v = max(-1.0, min(1.0, v))
            self.current_servo_val = v
            self.servo.value = v
            
            # High-frequency updates for smoothness
            time.sleep(0.01) 
            
        # Lock final position
        self.current_servo_val = target_val
        self.servo.value = target_val

class SocketServer:
    def __init__(self, hw):
        self.hw, self.in_progress = hw, False
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    def sequence_access_granted(self):
        self.in_progress = True
        log("DOOR", "ACCESS GRANTED - Starting 30s sequence", G)
        for _ in range(3): self.hw.play_buzzer(0.1); time.sleep(0.05)
        
        # 1. Open (20s)
        self.hw.set_servo_smooth(SERVO_OPEN, detach=False)
        self.hw.set_led(0, 1, 0); log("DOOR", "OPEN (0 deg) - Phase 1: 20s Solid Green", G)
        time.sleep(20)

        # --- START 10s UNIFIED GLIDE (0 -> 90 deg) ---
        log("DOOR", "Unified 10s Closing Glide Started...", Y)
        # One single background thread for the entire physical movement
        glide_thread = threading.Thread(
            target=self.hw.set_servo_smooth_timed, 
            args=(SERVO_CLOSED, 10.0), 
            daemon=True
        )
        glide_thread.start()

        # 2. Phase 2: Yellow (5s)
        log("DOOR", "Phase 2: Yellow Warning", Y)
        for _ in range(5):
            self.hw.set_led(1, 1, 0)
            self.hw.play_buzzer(0.1)
            time.sleep(0.9)

        # 3. Phase 3: Red Alert (5s)
        log("DOOR", "Phase 3: RED ALERT - Final Warning", R)
        for i in range(10): # Rapid 0.5s pulses
            self.hw.set_led(1, 0, 0)
            self.hw.play_buzzer(0.05)
            time.sleep(0.15)
            self.hw.set_led(0, 0, 0)
            time.sleep(0.3)

        glide_thread.join() # Wait for glide to finish

        # 4. Lock
        self.hw.set_led(1, 0, 0); self.hw.set_servo_smooth(SERVO_CLOSED, detach=True)
        self.hw.set_led(0, 0, 1); log("DOOR", "Door CLOSED - IDLE", B)
        self.in_progress = False

    def sequence_access_denied(self):
        self.in_progress = True
        log("DOOR", "ACCESS DENIED", R)
        self.hw.set_led(1, 0, 0); self.hw.play_buzzer(1.5); self.hw.set_led(0, 0, 1)
        self.in_progress = False

    def handle_client(self, conn, addr):
        log("NET", f"Connected: {addr}", C)
        try:
            while True:
                data = conn.recv(1024)
                if not data: break
                msg = data.decode('utf-8').strip()
                if msg == 'GET_DIST': conn.sendall(f"{self.hw.get_distance():.2f}\n".encode('utf-8'))
                elif self.in_progress: conn.sendall(b"BUSY\n")
                else:
                    if msg == 'STATE:IDLE': self.hw.set_led(0, 0, 1); conn.sendall(b"OK\n")
                    elif msg == 'STATE:VALIDATING':
                        self.hw.set_led(0, 1, 1); conn.sendall(b"OK\n")
                        threading.Thread(target=lambda: [self.hw.play_buzzer(0.05), time.sleep(0.05), self.hw.play_buzzer(0.05)], daemon=True).start()
                    elif msg == 'ACTION:OPEN':
                        threading.Thread(target=self.sequence_access_granted, daemon=True).start(); conn.sendall(b"OK\n")
                    elif msg == 'ACTION:DENIED':
                        threading.Thread(target=self.sequence_access_denied, daemon=True).start(); conn.sendall(b"OK\n")
        except: pass
        finally: conn.close(); log("NET", "Disconnected", DIM)

    def start(self):
        print(BANNER); self.server.bind(('0.0.0.0', 5005)); self.server.listen(5)
        log("NET", "Server ready on 5005", G)
        
        # Startup melody for hardware self-test
        for _ in range(3):
            self.hw.set_led(0, 1, 1); self.hw.play_buzzer(0.05); time.sleep(0.05); self.hw.set_led(0, 0, 0); time.sleep(0.05)
        
        self.hw.set_led(0, 0, 1) # IDLE
        try:
            while True:
                c, a = self.server.accept()
                threading.Thread(target=self.handle_client, args=(c, a), daemon=True).start()
        except KeyboardInterrupt: pass
        finally: GPIO.cleanup(); self.hw.set_led(0, 0, 0)

if __name__ == '__main__':
    SocketServer(HardwareController()).start()
