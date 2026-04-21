import socket
import threading
import time

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    print("WARNING: RPi.GPIO not found.")
    GPIO_AVAILABLE = False

try:
    from gpiozero import Servo, RGBLED
    from gpiozero.pins.pigpio import PiGPIOFactory
    GPIOZERO_AVAILABLE = True
except ImportError:
    print("WARNING: gpiozero not found.")
    GPIOZERO_AVAILABLE = False

# Hardware Pins
SERVO_PIN = 18
LED_R = 22
LED_G = 27
LED_B = 25
TRIG_PIN = 23
ECHO_PIN = 24
BUZZER_PIN = 17

# Servo positions (gpiozero maps -1.0 to 1.0)
# Your servo: 0 degrees = OPEN,  90 degrees = CLOSED
SERVO_OPEN   = -1.0   # gpiozero -1.0  →  0°  (fully open)
SERVO_CLOSED =  0.0   # gpiozero  0.0  →  90° (fully closed)

class HardwareController:
    def __init__(self):
        self.servo = None
        self.rgb = None
        self.current_servo_val = 0.0
        
        if GPIO_AVAILABLE:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            # Ultrasonic sensor pins (managed solely by RPi.GPIO)
            GPIO.setup(TRIG_PIN, GPIO.OUT)
            GPIO.setup(ECHO_PIN, GPIO.IN)
            GPIO.output(TRIG_PIN, False)
            # Buzzer
            GPIO.setup(BUZZER_PIN, GPIO.OUT)
            GPIO.output(BUZZER_PIN, False)
            time.sleep(0.5) # Allow ultrasonic to settle

        if GPIOZERO_AVAILABLE:
            factory = None
            try:
                factory = PiGPIOFactory()
                print("Using pigpio precision PWM.")
            except Exception:
                print("WARNING: pigpiod not running. Servo might jitter.")
            
            # Servo and LED only managed by gpiozero
            self.servo = Servo(SERVO_PIN, pin_factory=factory)
            self.rgb = RGBLED(red=LED_R, green=LED_G, blue=LED_B, active_high=True)
            
            self.servo.value = 0.0
            time.sleep(0.5)
            self.servo.value = None
            
            self.set_led(0, 0, 1) # Blue
            time.sleep(0.5) # Settling time

    def get_distance(self):
        """Measure distance using RPi.GPIO pulse-timing. Returns cm."""
        if not GPIO_AVAILABLE:
            return 60.0 # Mock value in mock mode
        
        GPIO.output(TRIG_PIN, True)
        time.sleep(0.00001)
        GPIO.output(TRIG_PIN, False)

        start_time = time.time()
        stop_time = time.time()
        timeout = start_time + 0.1

        while GPIO.input(ECHO_PIN) == 0:
            start_time = time.time()
            if start_time > timeout: return -1.0

        while GPIO.input(ECHO_PIN) == 1:
            stop_time = time.time()
            if stop_time > timeout: return -1.0

        return (stop_time - start_time) * 34300 / 2

    def play_buzzer(self, frequency, duration):
        if not GPIO_AVAILABLE:
            time.sleep(duration)
            return
            
        period = 1.0 / frequency
        delay = period / 2.0
        cycles = int(duration * frequency)
        for _ in range(cycles):
            GPIO.output(BUZZER_PIN, True)
            time.sleep(delay)
            GPIO.output(BUZZER_PIN, False)
            time.sleep(delay)

    def play_slide(self, start_freq, end_freq, duration):
        steps = 50
        step_duration = duration / steps
        freq_step = (end_freq - start_freq) / steps
        for i in range(steps):
            self.play_buzzer(start_freq + (i * freq_step), step_duration)

    def set_led(self, r, g, b):
        if self.rgb:
            self.rgb.color = (r, g, b)

    def set_servo_smooth(self, target_val):
        # Clamp target to safe bounds
        target_val = max(-1.0, min(1.0, target_val))

        if self.servo is None:
            self.current_servo_val = target_val
            return

        self.servo.value = self.current_servo_val
        diff = target_val - self.current_servo_val
        if diff == 0:
            self.servo.value = None
            return

        steps = int(abs(diff) / 0.01)
        if steps == 0: steps = 1
        step_val = diff / steps

        for _ in range(steps):
            self.current_servo_val += step_val
            # Clamp every step to prevent float drift from exceeding gpiozero bounds
            self.current_servo_val = max(-1.0, min(1.0, self.current_servo_val))
            self.servo.value = self.current_servo_val
            time.sleep(0.03)

        # Force exact target at end to clear any accumulated drift
        self.current_servo_val = target_val
        self.servo.value = self.current_servo_val
        time.sleep(0.05)
        self.servo.value = None  # Detach to prevent humming

    def set_servo_direct(self, val):
        """Directly set servo position with clamping (used in timed warning phases)."""
        val = max(-1.0, min(1.0, val))
        self.current_servo_val = val
        if self.servo:
            self.servo.value = val


class SocketServer:
    def __init__(self, hw):
        self.hw = hw
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.lockout_until = 0
        self.in_progress = False

    def sequence_access_granted(self):
        self.in_progress = True
        
        # Access granted rising melody
        for f in [(1000, 0.1), (1500, 0.1), (2000, 0.15)]:
            self.hw.play_buzzer(f[0], f[1])
        
        time.sleep(0.2)
        
        # --- OPEN DOOR (0 degrees) ---
        self.hw.set_servo_smooth(SERVO_OPEN)
        print("[DOOR] Door is now OPEN (0 degrees).")

        # ============================================================
        # PHASE 1: 0s - 20s  |  GREEN LED  |  Door fully OPEN (0°)
        # Silent. Door is wide open. User walks through freely.
        # ============================================================
        self.hw.set_led(0, 1, 0)  # Solid Green
        for remaining in range(20, 0, -1):
            print(f"[DOOR] 🟢 OPEN — closing in {remaining + 10}s")
            time.sleep(1)

        # ============================================================
        # PHASE 2: 20s - 25s  |  YELLOW LED  |  Door closing 0°→45°
        # Slow beep every second. Door starts closing noticeably.
        # ============================================================
        print("[DOOR] ⚠ YELLOW PHASE: 10s left — door starting to close!")
        for i in range(5):  # 5 seconds: 0°→45° (servo -1.0 → -0.5)
            remaining_total = 10 - i
            partial_val = SERVO_OPEN + (i * 0.1)   # -1.0 → -0.5 in 5 steps
            angle_deg = int((partial_val - SERVO_OPEN) * 90)  # 0° → 45°
            self.hw.set_servo_direct(partial_val)  # Clamped direct set

            print(f"[DOOR] 🟡 Closing in {remaining_total}s (Door at {angle_deg}°/90°)")
            self.hw.set_led(1, 1, 0)          # Solid Yellow
            self.hw.play_buzzer(1200, 0.15)   # Moderate single beep
            time.sleep(0.85)                  # Rest of the second

        # ============================================================
        # PHASE 3: 25s - 30s  |  RED FLASHING  |  Door closing 45°→90°
        # Rapid urgent beeps. Door rapidly closing. Last chance!
        # ============================================================
        print("[DOOR] 🔴 RED PHASE: 5s left — GET INSIDE NOW!")
        for i in range(5):  # 5 seconds: 45°→90° (servo -0.5 → 0.0)
            remaining_total = 5 - i
            partial_val = -0.5 + (i * 0.1)   # -0.5 → 0.0 in 5 steps
            angle_deg = int((partial_val - SERVO_OPEN) * 90)  # 45° → 90°
            self.hw.set_servo_direct(partial_val)  # Clamped direct set

            print(f"[DOOR] 🔴 CLOSING in {remaining_total}s (Door at {angle_deg}°/90°) — FINAL WARNING!")
            # Rapid double beep + flashing Red
            self.hw.set_led(1, 0, 0)          # Red ON
            self.hw.play_buzzer(1800, 0.08)   # First rapid beep
            time.sleep(0.1)
            self.hw.play_buzzer(1800, 0.08)   # Second rapid beep
            time.sleep(0.1)
            self.hw.set_led(0, 0, 0)          # Red OFF (flash)
            time.sleep(0.64)                  # Rest of the second

        # ============================================================
        # CLOSE: Final smooth close to exactly 90°  +  alert sound
        # ============================================================
        print("[DOOR] Closing door now → 90 degrees.")
        self.hw.set_led(1, 0, 0)  # Solid Red during close
        self.hw.play_slide(start_freq=2000, end_freq=500, duration=0.6)
        self.hw.set_servo_smooth(SERVO_CLOSED)  # Fully closed at 90°

        # Reset to Idle
        self.hw.set_led(0, 0, 1)  # Blue — IDLE
        print("[DOOR] Door CLOSED. System IDLE.")
        self.in_progress = False


    def sequence_access_denied(self):
        self.in_progress = True
        self.lockout_until = time.time() + 60
        self.hw.set_led(1, 0, 0) # Red
        self.hw.play_buzzer(400, 1.5)
        print("Module LOCKED for 60 seconds.")
        self.in_progress = False

    def handle_client(self, conn, addr):
        print(f"[TCP] Connected to {addr}")
        try:
            while True:
                data = conn.recv(1024)
                if not data: break
                
                msg = data.decode('utf-8').strip()
                
                if msg == 'GET_DIST':
                    conn.sendall(f"{self.hw.get_distance():.2f}\n".encode('utf-8'))
                    continue
                
                if time.time() < self.lockout_until:
                    self.hw.set_led(1, 0, 0)
                    conn.sendall(b"LOCKED\n")
                    continue

                if not self.in_progress:
                    if msg == 'STATE:IDLE':
                        self.hw.set_led(0, 0, 1)
                        conn.sendall(b"OK\n")
                    elif msg == 'STATE:VALIDATING':
                        self.hw.set_led(0, 1, 1)
                        # Quick double chirp thread
                        threading.Thread(target=lambda: [self.hw.play_buzzer(2000, 0.05), time.sleep(0.05), self.hw.play_buzzer(2000, 0.05)], daemon=True).start()
                        conn.sendall(b"OK\n")
                    elif msg == 'ACTION:OPEN':
                        threading.Thread(target=self.sequence_access_granted, daemon=True).start()
                        conn.sendall(b"OK\n")
                    elif msg == 'ACTION:DENIED':
                        threading.Thread(target=self.sequence_access_denied, daemon=True).start()
                        conn.sendall(b"OK\n")
                    else:
                        conn.sendall(b"UNKNOWN\n")
                else:
                    conn.sendall(b"BUSY\n")
        except Exception as e:
            print(f"[TCP] Client Error: {e}")
        finally:
            conn.close()

    def start(self):
        self.server.bind(('0.0.0.0', 5005))
        self.server.listen(5)
        print("Hardware Server listening on Port 5005")
        try:
            while True:
                conn, addr = self.server.accept()
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
        except KeyboardInterrupt:
            print("\nShutting down Server...")
        finally:
            self.server.close()
            if GPIO_AVAILABLE:
                try:
                    GPIO.cleanup([BUZZER_PIN])
                except:
                    pass

if __name__ == '__main__':
    hw_controller = HardwareController()
    server = SocketServer(hw_controller)
    server.start()
