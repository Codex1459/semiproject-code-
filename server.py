import socket
import time
import threading
from gpiozero import Servo, RGBLED, Buzzer
from gpiozero.pins.pigpio import PiGPIOFactory
import RPi.GPIO as GPIO

# ─────────────────────────────────────────────
#  ANSI Terminal Colors
# ─────────────────────────────────────────────
R  = "\033[91m";  G  = "\033[92m";  Y  = "\033[93m"
B  = "\033[94m";  M  = "\033[95m";  C  = "\033[96m"
W  = "\033[97m";  DIM = "\033[2m";  RST = "\033[0m"
BOLD = "\033[1m"

def log(tag, msg, color=W):
    ts = time.strftime("%H:%M:%S")
    print(f"{DIM}[{ts}]{RST} {color}{BOLD}[{tag}]{RST} {msg}")

BANNER = f"""
{M}╔══════════════════════════════════════════════════════╗
║  {W}{BOLD} ██████╗  █████╗ ███████╗██████╗ ██╗  ██████╗{M}       ║
║  {W}{BOLD} ██╔══██╗██╔══██╗██╔════╝██╔══██╗██║ ██╔═══██╗{M}      ║
║  {W}{BOLD} ██████╔╝███████║███████╗██████╔╝██║ ██║   ██║{M}      ║
║  {W}{BOLD} ██╔══██╗██╔══██║╚════██║██╔═══╝ ██║ ██║   ██║{M}      ║
║  {W}{BOLD} ██║  ██║██║  ██║███████║██║     ██║ ╚██████╔╝{M}      ║
║  {W}{BOLD} ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝     ╚═╝  ╚═════╝ {M}      ║
║                                                      ║
║  {B}{BOLD}  Hardware Controller  —  Raspberry Pi Server  {M}     ║
║  {DIM}  PWM Servo • RGB LED • Pulse Ultrasonic • Buzz {M}     ║
╚══════════════════════════════════════════════════════╝{RST}
"""

# ─────────────────────────────────────────────
#  Hardware Config (BCM Pins)
# ─────────────────────────────────────────────
SERVO_PIN  = 18
LED_R      = 22
LED_G      = 27
LED_B      = 25
TRIG_PIN   = 23
ECHO_PIN   = 24
BUZZER_PIN = 17

# Servo mapping (gpiozero: -1.0 to 1.0)
SERVO_OPEN   = -1.0  # 0°
SERVO_CLOSED = 0.0   # 90°

class HardwareController:
    def __init__(self):
        log("HW", "Initializing components...", C)
        try:
            factory = PiGPIOFactory()
            self.servo = Servo(SERVO_PIN, pin_factory=factory)
            self.rgb   = RGBLED(red=LED_R, green=LED_G, blue=LED_B)
            self.buzz  = Buzzer(BUZZER_PIN)
        except Exception as e:
            log("HW", f"Initialization Error: {e}", R)
            self.servo = self.rgb = self.buzz = None

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(TRIG_PIN, GPIO.OUT)
        GPIO.setup(ECHO_PIN, GPIO.IN)
        
        self.current_servo_val = SERVO_CLOSED
        if self.servo: self.servo.value = None # Idle

    # ─── Sensors ───────────────────────────────
    def get_distance(self):
        """ Manual pulse timing for ultrasonic (fixes edge detection conflicts) """
        try:
            GPIO.output(TRIG_PIN, True)
            time.sleep(0.00001)
            GPIO.output(TRIG_PIN, False)
            
            start = time.time()
            stop  = time.time()
            
            timeout = time.time() + 0.1
            while GPIO.input(ECHO_PIN) == 0:
                start = time.time()
                if start > timeout: return -1
                
            while GPIO.input(ECHO_PIN) == 1:
                stop = time.time()
                if stop > timeout: return -1
                
            return (stop - start) * 17150
        except:
            return -1

    # ─── Actuators ─────────────────────────────
    def set_led(self, r, g, b):
        if self.rgb: self.rgb.color = (r, g, b)

    def play_buzzer(self, freq, duration):
        if not self.buzz: return
        self.buzz.on()
        time.sleep(duration)
        self.buzz.off()

    def play_slide(self, start_f, end_f, duration):
        steps = 20
        step_d = duration / steps
        f_step = (end_f - start_f) / steps
        for i in range(steps):
            self.play_buzzer(start_f + (i * f_step), step_d)

    # ─── Servo ─────────────────────────────────
    def set_servo_smooth(self, target_val, detach=True):
        target_val = max(-1.0, min(1.0, target_val))
        if not self.servo: return

        self.servo.value = self.current_servo_val
        diff = target_val - self.current_servo_val
        if diff == 0:
            if detach: self.servo.value = None
            return

        steps = max(1, int(abs(diff) / 0.01))
        step_val = diff / steps
        for _ in range(steps):
            self.current_servo_val += step_val
            self.current_servo_val = max(-1.0, min(1.0, self.current_servo_val))
            self.servo.value = self.current_servo_val
            time.sleep(0.02)

        self.servo.value = target_val
        if detach: self.servo.value = None

    def set_servo_smooth_timed(self, target_val, duration):
        target_val = max(-1.0, min(1.0, target_val))
        if not self.servo: return
        
        start_val = self.current_servo_val
        diff = target_val - start_val
        hz = 50
        steps = max(1, int(duration * hz))
        step_delay = duration / steps

        self.servo.value = start_val
        for i in range(1, steps + 1):
            val = max(-1.0, min(1.0, start_val + diff * (i / steps)))
            self.current_servo_val = val
            self.servo.value = val
            time.sleep(step_delay)

class SocketServer:
    def __init__(self, hw):
        self.hw = hw
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.in_progress = False

    def sequence_access_granted(self):
        self.in_progress = True
        log("DOOR", "ACCESS GRANTED sequence started", G)
        
        # Audio Brand
        for f in [1000, 1500, 2000]: self.hw.play_buzzer(f, 0.1)
        
        # 1. Open
        self.hw.set_servo_smooth(SERVO_OPEN, detach=False)
        self.hw.set_led(0, 1, 0)
        log("DOOR", "Door OPEN (0°). Phase 1: 20s Solid Green.", G)
        time.sleep(20)

        # 2. Phase 2: Yellow / Smooth close to 45°
        log("DOOR", "Phase 2: 5s Yellow. Gliding to 45°.", Y)
        threading.Thread(target=self.hw.set_servo_smooth_timed, args=(-0.5, 5.0), daemon=True).start()
        for _ in range(5):
            self.hw.set_led(1, 1, 0)
            self.hw.play_buzzer(1200, 0.1)
            time.sleep(0.9)

        # 3. Phase 3: Red Flash / Smooth close to 90°
        log("DOOR", "Phase 3: 5s Red Flash. FINAL WARNING.", R)
        threading.Thread(target=self.hw.set_servo_smooth_timed, args=(SERVO_CLOSED, 5.0), daemon=True).start()
        for _ in range(5):
            self.hw.set_led(1, 0, 0); self.hw.play_buzzer(1800, 0.05); time.sleep(0.1)
            self.hw.play_buzzer(1800, 0.05); time.sleep(0.1)
            self.hw.set_led(0, 0, 0); time.sleep(0.75)

        # 4. Final Lock
        self.hw.set_led(1, 0, 0)
        self.hw.play_slide(2000, 500, 0.6)
        self.hw.set_servo_smooth(SERVO_CLOSED, detach=True)
        self.hw.set_led(0, 0, 1)
        log("DOOR", "Sequence complete. System IDLE (Blue).", B)
        self.in_progress = False

    def sequence_access_denied(self):
        self.in_progress = True
        log("DOOR", "ACCESS DENIED sequence started", R)
        self.hw.set_led(1, 0, 0)
        self.hw.play_buzzer(400, 1.5)
        self.hw.set_led(0, 0, 1)
        self.in_progress = False

    def handle_client(self, conn, addr):
        log("NET", f"Client connected: {addr}", C)
        try:
            while True:
                data = conn.recv(1024)
                if not data: break
                msg = data.decode('utf-8').strip()

                if msg == 'GET_DIST':
                    conn.sendall(f"{self.hw.get_distance():.2f}\n".encode('utf-8'))
                elif self.in_progress:
                    conn.sendall(b"BUSY\n")
                else:
                    if msg == 'STATE:IDLE':
                        self.hw.set_led(0, 0, 1); conn.sendall(b"OK\n")
                    elif msg == 'STATE:VALIDATING':
                        self.hw.set_led(0, 1, 1)
                        threading.Thread(target=lambda: [self.hw.play_buzzer(2000,0.05), time.sleep(0.05), self.hw.play_buzzer(2000,0.05)], daemon=True).start()
                        conn.sendall(b"OK\n")
                    elif msg == 'ACTION:OPEN':
                        threading.Thread(target=self.sequence_access_granted, daemon=True).start()
                        conn.sendall(b"OK\n")
                    elif msg == 'ACTION:DENIED':
                        threading.Thread(target=self.sequence_access_denied, daemon=True).start()
                        conn.sendall(b"OK\n")
                    else:
                        conn.sendall(b"UNKNOWN\n")
        except Exception as e: log("NET", f"Error: {e}", R)
        finally: conn.close(); log("NET", "Client disconnected.", DIM)

    def start(self):
        print(BANNER)
        self.server.bind(('0.0.0.0', 5005))
        self.server.listen(5)
        log("NET", f"Server listening on port 5005...", G)
        self.hw.set_led(0, 0, 1) # IDLE
        try:
            while True:
                conn, addr = self.server.accept()
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
        except KeyboardInterrupt:
            log("SYS", "Shutting down...", DIM)
        finally:
            GPIO.cleanup()
            self.hw.set_led(0, 0, 0)

if __name__ == '__main__':
    hw = HardwareController()
    server = SocketServer(hw)
    server.start()
