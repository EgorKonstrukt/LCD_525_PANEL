#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <EEPROM.h>

LiquidCrystal_I2C lcd(0x27, 16, 2);

const int DEFAULT_BUZZER_PIN = 3;
const int LED_PIN = 13;

#define TONE_SAMPLE_RATE 32000UL

const uint8_t EE_MAGIC = 0x5A;

int buzzerPin = DEFAULT_BUZZER_PIN;
int buzzerVolume = 80;
int buzzerFreq = 2500;
int buzzerSpeed = 100;
bool buzzerPassive = true;
bool ledEnabled = true;

volatile uint8_t *tonePort = 0;
volatile uint8_t toneBit = 0;
volatile uint32_t toneCounter = 0;
volatile uint32_t tonePeriod = 0;
volatile uint32_t toneOnTime = 0;
volatile uint8_t toneActive = 0;

ISR(TIMER1_COMPA_vect) {
  if (toneActive) {
    toneCounter++;
    if (toneCounter >= tonePeriod) {
      toneCounter = 0;
    }
    if (toneCounter < toneOnTime) {
      *tonePort |= toneBit;
    } else {
      *tonePort &= ~toneBit;
    }
  }
}

const unsigned long DATA_TIMEOUT = 5000;
unsigned long lastDataTime = 0;
bool hasData = false;
bool isConnected = false;

String line1 = "";
String line2 = "";
bool screenDirty = false;

const char* scrollText = "NOT CONNECTED - WAITING FOR DATA   ";
int scrollPos = 0;
unsigned long previousScrollMillis = 0;
const int scrollSpeed = 300;

const char* BOOT_TITLE = "LCD525 PANEL";
const uint8_t SPIN_FRAMES[8][8] = {
  {0x00, 0x00, 0x00, 0x07, 0x07, 0x00, 0x00, 0x00},
  {0x00, 0x00, 0x00, 0x04, 0x06, 0x01, 0x00, 0x00},
  {0x00, 0x00, 0x00, 0x04, 0x04, 0x04, 0x04, 0x00},
  {0x00, 0x00, 0x00, 0x04, 0x06, 0x10, 0x00, 0x00},
  {0x00, 0x00, 0x00, 0x1C, 0x1C, 0x00, 0x00, 0x00},
  {0x00, 0x10, 0x08, 0x04, 0x04, 0x00, 0x00, 0x00},
  {0x04, 0x04, 0x04, 0x04, 0x04, 0x00, 0x00, 0x00},
  {0x00, 0x01, 0x02, 0x04, 0x04, 0x00, 0x00, 0x00}
};
unsigned long bootStartTime = 0;
bool bootAnimationDone = false;
unsigned long lastBootDraw = 0;
int spinIndex = 0;
unsigned long lastSpinMillis = 0;

String inputString = "";

const int MAX_STEPS = 40;
int beepDur[MAX_STEPS];
int beepRatio[MAX_STEPS];
int beepCount = 0;
int beepIndex = 0;
unsigned long beepTimer = 0;
bool beeping = false;
bool pendingConnectChime = false;

int ledLoadDuty = 0;
bool ledAlertBlink = false;
unsigned long ledTimer = 0;
unsigned long ledAlertUntil = 0;

void lcdPrintRow(int row, const String& text) {
  lcdPrintRowAt(row, 0, text, 16);
}

void lcdPrintRowAt(int row, int col, const String& text, int width) {
  lcd.setCursor(col, row);
  char buf[17];
  int n = text.length();
  int i;
  for (i = 0; i < width; i++) {
    buf[i] = (i < n) ? text[i] : ' ';
  }
  buf[width] = 0;
  lcd.print(buf);
}

void setup() {
  loadSettings();
  lcd.init();
  lcd.backlight();
  for (int i = 0; i < 8; i++) {
    lcd.createChar(i, (uint8_t*)SPIN_FRAMES[i]);
  }
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  setBuzzerPin(buzzerPin);
  toneEngineInit();
  Serial.begin(9600);
  inputString.reserve(64);
  bootStartTime = millis();
  bootAnimationDone = false;
  lastBootDraw = 0;
  startBeep("wake");
}

void loop() {
  processSerial();

  bool nowConnected = hasData && (millis() - lastDataTime <= DATA_TIMEOUT);

  if (nowConnected != isConnected) {
    isConnected = nowConnected;
    if (isConnected) {
      if (beeping) {
        pendingConnectChime = true;
      } else {
        startBeep("chime_up");
      }
      ledAlertBlink = false;
    } else {
      startBeep("chime_down");
    }
    lcd.clear();
    screenDirty = true;
  }

  unsigned long now = millis();
  bool bootActive = !bootAnimationDone && !isConnected &&
                    (now - bootStartTime < 2400UL);

  if (bootActive) {
    updateBootAnimation();
  } else if (!bootAnimationDone) {
    bootAnimationDone = true;
    if (!isConnected) {
      lcd.clear();
      previousScrollMillis = 0;
    }
  }

  if (!isConnected) {
    if (!bootActive) {
      updateScrollingText();
    }
  } else if (screenDirty) {
    lcdPrintRowAt(0, 0, line1, 16);
    lcdPrintRowAt(1, 0, line2, 15);
    screenDirty = false;
  }

  updateSpinner();
  updateBeep();
  updateLed();
}

void processSerial() {
  int nl;
  while ((nl = inputString.indexOf('\n')) >= 0 ||
         (nl = inputString.indexOf('\r')) >= 0) {
    String line = inputString.substring(0, nl);
    line.trim();
    if (line.length() > 0) {
      processInput(line);
    }
    inputString.remove(0, nl + 1);
    if (inputString.length() > 96) {
      inputString = "";
      break;
    }
  }
}

void processInput(String input) {
  lastDataTime = millis();
  hasData = true;

  if (input.startsWith("D:")) {
    String body = input.substring(2);
    int sep = body.indexOf('|');
    if (sep != -1) {
      String nl1 = body.substring(0, sep);
      String nl2 = body.substring(sep + 1);
      nl1.trim();
      nl2.trim();
      if (nl1.length() > 16) nl1 = nl1.substring(0, 16);
      if (nl2.length() > 16) nl2 = nl2.substring(0, 16);
      if (nl1 != line1 || nl2 != line2) {
        line1 = nl1;
        line2 = nl2;
        screenDirty = true;
      }
    }
  } else if (input.startsWith("A:")) {
    String mode = input.substring(2);
    mode.trim();
    startBeep(mode);
    if (ledEnabled) {
      ledAlertBlink = true;
      ledTimer = millis();
      ledAlertUntil = millis() + 2000;
      digitalWrite(LED_PIN, HIGH);
    }
  } else if (input.startsWith("B:")) {
    String mode = input.substring(2);
    mode.trim();
    startBeep(mode);
  } else if (input.startsWith("X:")) {
    ledLoadDuty = constrain(input.substring(2).toInt(), 0, 100);
  } else if (input.startsWith("P:")) {
    int p = input.substring(2).toInt();
    if (p >= 2 && p <= 13) {
      setBuzzerPin(p);
      saveSettings();
    }
  } else if (input.startsWith("V:")) {
    buzzerVolume = constrain(input.substring(2).toInt(), 0, 100);
    saveSettings();
  } else if (input.startsWith("F:")) {
    buzzerFreq = constrain(input.substring(2).toInt(), 100, 10000);
    saveSettings();
  } else if (input.startsWith("S:")) {
    buzzerSpeed = constrain(input.substring(2).toInt(), 25, 400);
    saveSettings();
  } else if (input.startsWith("T:")) {
    String t = input.substring(2);
    t.trim();
    buzzerPassive = (t == "passive");
    saveSettings();
  } else if (input.startsWith("L:")) {
    String m = input.substring(2);
    m.trim();
    if (m == "on") {
      ledEnabled = true;
    } else if (m == "off") {
      ledEnabled = false;
      ledAlertBlink = false;
      digitalWrite(LED_PIN, LOW);
    }
  } else if (input.startsWith("Q:")) {
    Serial.print("S:");
    Serial.print(buzzerPin);
    Serial.print(",");
    Serial.print(buzzerPassive ? 1 : 0);
    Serial.print(",");
    Serial.print(buzzerVolume);
    Serial.print(",");
    Serial.print(buzzerFreq);
    Serial.print(",");
    Serial.print(buzzerSpeed);
    Serial.print(",");
    Serial.println(beeping ? 1 : 0);
  }
}

void loadSettings() {
  if (EEPROM.read(0) != EE_MAGIC) {
    return;
  }
  int p = EEPROM.read(1);
  if (p >= 2 && p <= 13) {
    buzzerPin = p;
  }
  uint8_t t = EEPROM.read(2);
  if (t == 0 || t == 1) {
    buzzerPassive = (t == 1);
  }
  uint8_t v = EEPROM.read(3);
  if (v <= 100) {
    buzzerVolume = v;
  }
  uint16_t f = EEPROM.read(4) | (uint16_t)(EEPROM.read(5) << 8);
  if (f >= 100 && f <= 10000) {
    buzzerFreq = f;
  }
  uint16_t sp = EEPROM.read(6) | (uint16_t)(EEPROM.read(7) << 8);
  if (sp >= 25 && sp <= 400) {
    buzzerSpeed = sp;
  }
}

void saveSettings() {
  if (EEPROM.read(0) != EE_MAGIC) {
    EEPROM.write(0, EE_MAGIC);
  }
  if (EEPROM.read(1) != (uint8_t)buzzerPin) {
    EEPROM.write(1, (uint8_t)buzzerPin);
  }
  uint8_t pt = buzzerPassive ? 1 : 0;
  if (EEPROM.read(2) != pt) {
    EEPROM.write(2, pt);
  }
  if (EEPROM.read(3) != (uint8_t)buzzerVolume) {
    EEPROM.write(3, (uint8_t)buzzerVolume);
  }
  uint16_t curF = EEPROM.read(4) | (uint16_t)(EEPROM.read(5) << 8);
  if (curF != (uint16_t)buzzerFreq) {
    EEPROM.write(4, (uint8_t)(buzzerFreq & 0xFF));
    EEPROM.write(5, (uint8_t)((buzzerFreq >> 8) & 0xFF));
  }
  uint16_t curS = EEPROM.read(6) | (uint16_t)(EEPROM.read(7) << 8);
  if (curS != (uint16_t)buzzerSpeed) {
    EEPROM.write(6, (uint8_t)(buzzerSpeed & 0xFF));
    EEPROM.write(7, (uint8_t)((buzzerSpeed >> 8) & 0xFF));
  }
}

void setBuzzerPin(int pin) {
  buzzerPin = pin;
  tonePort = portOutputRegister(digitalPinToPort(pin));
  toneBit = digitalPinToBitMask(pin);
  pinMode(pin, OUTPUT);
  *tonePort &= ~toneBit;
}

void toneEngineInit() {
  TCCR1A = 0;
  TCCR1B = 0;
  TCNT1 = 0;
  OCR1A = (uint16_t)((F_CPU / TONE_SAMPLE_RATE) - 1);
  TCCR1B = (1 << WGM12) | (1 << CS10);
  TIMSK1 = (1 << OCIE1A);
}

void setTone(int frequency, int volume) {
  if (frequency <= 0 || volume <= 0) {
    stopTone();
    return;
  }
  uint32_t period = TONE_SAMPLE_RATE / (uint32_t)frequency;
  if (period < 2) period = 2;
  tonePeriod = period;
  toneOnTime = period * (uint32_t)volume / 200UL;
  if (toneOnTime == 0) toneOnTime = 1;
  toneCounter = 0;
  toneActive = 1;
}

void stopTone() {
  toneActive = 0;
  if (tonePort != 0) {
    *tonePort &= ~toneBit;
  }
}

void buzzerOn(int ratio) {
  if (buzzerPassive) {
    uint32_t f = (uint32_t)buzzerFreq * (uint32_t)ratio / 1000UL;
    if (f < 100) f = 100;
    setTone((int)f, buzzerVolume);
  } else {
    digitalWrite(buzzerPin, HIGH);
  }
}

void buzzerOff() {
  if (buzzerPassive) {
    stopTone();
  } else {
    digitalWrite(buzzerPin, LOW);
  }
}

void addStep(int ms, int ratio) {
  int v = ms * 100 / buzzerSpeed;
  if (v < 10) v = 10;
  beepDur[beepCount] = v;
  beepRatio[beepCount] = ratio;
  beepCount++;
}

void playStep() {
  if (beepRatio[beepIndex] <= 0) {
    buzzerOff();
  } else {
    buzzerOn(beepRatio[beepIndex]);
  }
}

void startBeep(String mode) {
  beepCount = 0;
  beepIndex = 0;
  if (mode == "short") {
    addStep(50, 1000);
  } else if (mode == "long") {
    addStep(500, 1000);
  } else if (mode == "double") {
    addStep(120, 1000); addStep(120, 0); addStep(120, 1000);
  } else if (mode == "triple") {
    addStep(100, 1000); addStep(80, 0); addStep(100, 1000);
    addStep(80, 0); addStep(100, 1000);
  } else if (mode == "rapid") {
    for (int i = 0; i < 6; i++) {
      addStep(40, 1000);
      addStep(70, 0);
    }
  } else if (mode == "chime_up") {
    addStep(150, 1000); addStep(50, 0); addStep(150, 1260);
    addStep(50, 0); addStep(200, 1498);
  } else if (mode == "chime_down") {
    addStep(150, 1498); addStep(50, 0); addStep(150, 1260);
    addStep(50, 0); addStep(200, 1000);
  } else if (mode == "siren") {
    for (int i = 0; i < 4; i++) {
      addStep(120, 1400); addStep(60, 0); addStep(120, 700); addStep(60, 0);
    }
  } else if (mode == "wake") {
    addStep(120, 1000); addStep(50, 0); addStep(120, 1122);
    addStep(50, 0); addStep(120, 1260); addStep(50, 0); addStep(180, 1498);
  } else if (mode == "buzz") {
    addStep(200, 500); addStep(80, 0); addStep(200, 500);
  } else if (mode == "notification") {
    addStep(120, 1260); addStep(60, 0); addStep(200, 1498);
  } else if (mode == "success") {
    addStep(100, 1000); addStep(40, 0); addStep(100, 1260);
    addStep(40, 0); addStep(180, 1498);
  } else if (mode == "sad") {
    addStep(160, 1498); addStep(80, 0); addStep(160, 1260);
    addStep(80, 0); addStep(160, 1000); addStep(300, 0);
  } else if (mode == "alarm") {
    for (int i = 0; i < 4; i++) {
      addStep(90, 1498); addStep(90, 700);
    }
  } else if (mode == "rising") {
    addStep(80, 1000); addStep(80, 1122); addStep(80, 1260);
    addStep(80, 1335); addStep(120, 1498);
  } else if (mode == "falling") {
    addStep(80, 1498); addStep(80, 1335); addStep(80, 1260);
    addStep(80, 1122); addStep(120, 1000);
  } else if (mode == "doorbell") {
    addStep(180, 1498); addStep(120, 0); addStep(220, 1122);
  } else if (mode == "sos") {
    for (int i = 0; i < 3; i++) { addStep(80, 1000); addStep(80, 0); }
    addStep(240, 0);
    for (int i = 0; i < 3; i++) { addStep(240, 1000); addStep(80, 0); }
    addStep(240, 0);
    for (int i = 0; i < 3; i++) { addStep(80, 1000); addStep(80, 0); }
  } else if (mode == "fanfare") {
    addStep(100, 1000); addStep(40, 0); addStep(100, 1260);
    addStep(40, 0); addStep(100, 1498); addStep(40, 0); addStep(180, 1890);
  } else if (mode == "game_over") {
    addStep(200, 1498); addStep(100, 0); addStep(200, 1260);
    addStep(100, 0); addStep(200, 1000); addStep(300, 0);
  } else {
    return;
  }
  beepTimer = millis();
  beeping = true;
  playStep();
}

void updateBeep() {
  if (!beeping) {
    if (pendingConnectChime) {
      pendingConnectChime = false;
      startBeep("chime_up");
    }
    return;
  }
  unsigned long now = millis();
  if (now - beepTimer >= (unsigned long)beepDur[beepIndex]) {
    beepIndex++;
    if (beepIndex >= beepCount) {
      buzzerOff();
      beeping = false;
      if (pendingConnectChime) {
        pendingConnectChime = false;
        startBeep("chime_up");
      }
      return;
    }
    beepTimer = now;
    playStep();
  }
}

void updateLed() {
  if (!isConnected) {
    digitalWrite(LED_PIN, LOW);
    return;
  }
  if (!ledEnabled) {
    digitalWrite(LED_PIN, LOW);
    return;
  }
  unsigned long now = millis();
  if (ledAlertBlink) {
    if (now >= ledAlertUntil) {
      ledAlertBlink = false;
    } else {
      if (now - ledTimer >= 150) {
        ledTimer = now;
        digitalWrite(LED_PIN, !digitalRead(LED_PIN));
      }
      return;
    }
  }
  unsigned long cycle = 500;
  unsigned long onTime = (unsigned long)ledLoadDuty * cycle / 100;
  unsigned long pos = now % cycle;
  digitalWrite(LED_PIN, (pos < onTime) ? HIGH : LOW);
}

void updateScrollingText() {
  unsigned long currentMillis = millis();
  if (currentMillis - previousScrollMillis >= scrollSpeed) {
    previousScrollMillis = currentMillis;
    lcd.setCursor(0, 0);
    lcd.print("NOT CONNECTED");
    lcd.setCursor(0, 1);
    String line = "";
    for (int i = 0; i < 15; i++) {
      int idx = (scrollPos + i) % strlen(scrollText);
      line += scrollText[idx];
    }
    lcd.print(line);
    scrollPos++;
    if ((unsigned int)scrollPos >= strlen(scrollText)) {
      scrollPos = 0;
    }
  }
}

void updateBootAnimation() {
  unsigned long now = millis();
  if (now - lastBootDraw < 40) {
    return;
  }
  lastBootDraw = now;
  unsigned long elapsed = now - bootStartTime;

  int titleLen = strlen(BOOT_TITLE);
  int typed = elapsed / 60;
  if (typed > titleLen) typed = titleLen;
  bool cursorOn = ((elapsed / 300) % 2) == 0;

  char buf[17];
  int i;
  for (i = 0; i < 16; i++) {
    if (i < typed) {
      buf[i] = BOOT_TITLE[i];
    } else if (i == typed && cursorOn) {
      buf[i] = '_';
    } else {
      buf[i] = ' ';
    }
  }
  buf[16] = 0;
  lcd.setCursor(0, 0);
  lcd.print(buf);

  int spin = (elapsed / 350) % 8;
  int filled = elapsed / 100;
  if (filled > 15) filled = 15;
  for (i = 0; i < 16; i++) {
    buf[i] = (i < filled) ? '#' : ' ';
  }
  buf[16] = 0;
  lcd.setCursor(0, 1);
  lcd.print(buf);
  lcd.setCursor(15, 1);
  lcd.write(spin);
}

void updateSpinner() {
  if (!bootAnimationDone) {
    return;
  }
  unsigned long now = millis();
  if (now - lastSpinMillis < 350) {
    return;
  }
  lastSpinMillis = now;
  spinIndex = (spinIndex + 1) % 8;
  lcd.setCursor(15, 1);
  lcd.write(spinIndex);
}

void serialEvent() {
  while (Serial.available()) {
    inputString += (char)Serial.read();
  }
}
