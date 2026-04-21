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
SERVO_OPEN   = -1.0   # gpiozero -1.0  â†’  0Â°  (fully open)
SERVO_CLOSED =  0.0   # gpiozero  0.0  â†’  90Â° (fully closed)

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

    def set_servo_smooth(self, target_val, detach=True):
        """Move servo smoothly to target. Set detach=False to keep engaged."""
        target_val = max(-1.0, min(1.0, target_val))

        if self.servo is None:
            self.current_servo_val = target_val
            return

        self.servo.value = self.current_servo_val
        diff = target_val - self.current_servo_val
        if diff == 0:
            if detach:
                self.servo.value = None
            return

        steps = int(abs(diff) / 0.01)
        if steps == 0: steps = 1
        step_val = diff / steps

        for _ in range(steps):
            self.current_servo_val += step_val
            self.current_servo_val = max(-1.0, min(1.0, self.current_servo_val))
            self.servo.value = self.current_servo_val
            time.sleep(0.03)

        self.current_servo_val = target_val
        self.servo.value = self.current_servo_val
        time.sleep(0.05)
        if detach:
            self.servo.value = None  # Detach to prevent humming

    def set_servo_smooth_timed(self, target_val, duration):
        """
        Butter-smooth servo move from current position to target over exactly
        `duration` seconds. Runs at ~50 updates/second. Does NOT detach on
        completion â€” caller is responsible for detaching if needed.
        """
        target_val = max(-1.0, min(1.0, target_val))

        if self.servo is None:
            time.sleep(duration)
            self.current_servo_val = target_val
            return

        start_val = self.current_servo_val
        diff = target_val - start_val
        if diff == 0:
            time.sleep(duration)
            return

        hz = 50                          # Updates per second
        steps = max(1, int(duration * hz))
        step_delay = duration / steps

        self.servo.value = start_val     # Engage before starting
        for i in range(1, steps + 1):
            val = start_val + diff * (i / steps)
            val = max(-1.0, min(1.0, val))
            self.current_servo_val = val
            self.servo.value = val
            time.sleep(step_delay)

        # Lock in exact target
        self.current_servo_val = target_val
        self.servo.value = target_val


class SocketServer:
    def __init__(self, hw):
        self.hw = hw
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.in_progress = False
    def sequence_access_granted(self):
        self.in_progress = True

        # ?? IMMEDIATE GREEN (no delay at all)
        self.hw.set_led(0, 1, 0)
        print("[DOOR] Access granted ï¿½ GREEN ON instantly")

        # ?? Play sound in background (non-blocking)
        threading.Thread(
            target=lambda: [self.hw.play_buzzer(f, d) for f, d in [(1000, 0.1), (1500, 0.1), (2000, 0.15)]],
            daemon=True
        ).start()

        time.sleep(0.1)  # tiny delay just for stability

        # ============================================================
        # ?? OPEN DOOR ? then CUT POWER
        # ============================================================
        self.hw.set_servo_smooth(SERVO_OPEN, detach=True)
        self.hw.current_servo_val = SERVO_OPEN

        print("[DOOR] Door OPEN (power OFF)")

        total_time = 25

        # ============================================================
        # ? WAIT (SERVO POWER OFF)
        # ============================================================
        for remaining in range(total_time, 0, -1):

            if remaining > 10:
                self.hw.set_led(0, 1, 0)

            elif remaining > 5:
                self.hw.set_led(1, 1, 0)
                self.hw.play_buzzer(1200, 0.1)

            else:
                self.hw.set_led(1, 0, 0)
                self.hw.play_buzzer(1800, 0.07)
                time.sleep(0.1)
                self.hw.play_buzzer(1800, 0.07)

            print(f"[DOOR] Closing in {remaining}s")
            time.sleep(1)

        # ============================================================
        # ?? TIME UP ? POWER ON ? CLOSE ? POWER OFF
        # ============================================================
        print("[DOOR] Time up ï¿½ closing")

        self.hw.set_led(1, 0, 0)
        self.hw.play_slide(2000, 500, 0.6)

        # Re-attach servo (power ON)
        if self.hw.servo:
            self.hw.servo.value = self.hw.current_servo_val
            time.sleep(0.2)

        # Close door
        self.hw.set_servo_smooth(SERVO_CLOSED, detach=True)
        self.hw.current_servo_val = SERVO_CLOSED

        print("[DOOR] Door CLOSED (power OFF)")

        # Back to idle
        self.hw.set_led(0, 0, 1)

        self.in_progress = False
    def sequence_access_denied(self):
            self.in_progress = True
            self.hw.set_led(1, 0, 0)        # Red
            self.hw.play_buzzer(400, 1.5)   # Error buzz
            print("[DOOR] Access denied. Returning to IDLE.")
            self.hw.set_led(0, 0, 1)        # Back to Blue immediately
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
