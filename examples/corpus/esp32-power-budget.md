# ESP32 deep-sleep power budget

Measured on a bare ESP32-WROOM-32 dev board, 3.3 V rail, USB unplugged,
only the on-board regulator powered.

## Numbers

- Active, Wi-Fi off, 240 MHz: **48 mA**
- Modem-sleep, 80 MHz: **20 mA**
- Light-sleep: **0.8 mA**
- Deep-sleep: **10 µA**

The dev board's regulator and USB-serial chip dominate deep-sleep current.
A bare module reaches **5 µA** because that quiescent load is gone.

## Design consequence

A 2000 mAh cell with a 10 µA floor lasts about 22 years on standby, so the
sleep floor is never the limiting factor on a dev board — the wake-up duty
cycle is.

Waking once per minute to read a sensor and transmit costs roughly:

- 0.4 s at 48 mA = 5.3 µAh per wake
- 60 wakes per hour = 320 µAh
- plus 60 µA standby = 380 µAh per hour total

At that rate the same cell lasts about 5 months.

## Gotchas

- `gpio_hold_en()` must be called before sleep or the pins float and leak.
- The UART keeps drawing current unless `uart_set_wakeup_threshold` is
  configured; measured 2 mA extra during deep sleep without it.
- Battery voltage must be sampled *before* the radio starts, not after,
  because the transmit burst sags the rail by ~200 mV.
