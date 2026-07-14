# RF Safety Pre-Flight Checklist

Complete this checklist **before** any `walk_spoof.py --tx` invocation.
Every item is a hard gate; a single failure aborts the operation.

## Legal + regulatory

- [ ] The engagement's written RoE names the specific chamber/cage
      fixture ID we are about to use. Cross-reference
      `plan/roe.json:machine_enforcement.rf.gnss.fixture_id`.
- [ ] The RoE names GNSS RF transmission explicitly. Generic "RF
      testing" language is insufficient — GNSS bands are ITU-protected
      (RR 5.208A / 5.328B / 5.443B; NIST IR 8323 §3.2).
- [ ] The operator running this test has a current ITU / national
      licence covering the L-band (1.1–1.6 GHz) or is operating under
      a research exemption logged in the RoE annex.
- [ ] The chamber's operating licence for the day is on file with the
      facility manager. Copy in `findings/gnss/<utc>-tx/licence.pdf`.

## Physical containment

- [ ] Chamber-door interlock is engaged AND its status is exposed to
      the test PC (fail-closed relay). If the door opens mid-test the
      TX must cut within one modulation frame.
- [ ] Screen-room isolation ≥ 60 dB across the target band, measured
      within the last 12 months. Attach the sweep to
      `findings/gnss/<utc>-tx/isolation.csv`.
- [ ] Antenna is inside the chamber and terminated into a dummy load
      or an authorized receiver-under-test — never radiating to a
      chamber door or window.

## EIRP + spectral discipline

- [ ] Peak EIRP calculated from TX power + antenna gain − cable loss
      is below the facility's chamber EIRP limit (typically −20 dBm
      referenced to a 1 kHz measurement bandwidth for GNSS
      simulations).
- [ ] Out-of-band emissions are attenuated ≥ 40 dB at ±10 MHz from
      the target GNSS band centre (L1 = 1575.42 MHz, L5 = 1176.45 MHz,
      E6 = 1278.75 MHz). Attach spectrum-analyzer screenshot.
- [ ] The simulator's oscillator has been referenced to the chamber
      GPSDO within the last 24 h — a free-running SDR drifts and can
      alias into adjacent aeronautical / satcom bands.

## Network segregation

- [ ] The test PC is on the isolated chamber VLAN, not the Decepticon
      management or engagement network.
- [ ] The test PC's clock source is the chamber GPSDO or PTP grandmaster,
      not NTP-over-Internet. Injected GNSS drift can feed back into
      NTP and drift the chamber clock itself.
- [ ] No cellular / Wi-Fi / Bluetooth radios are enabled on the test
      PC or the receiver-under-test. Airplane-mode both.

## Emergency stop

- [ ] The chamber's E-stop (physical mushroom) is confirmed to cut TX
      power upstream of the SDR — verified by pressing it before the
      test and checking the SDR loses PLL lock.
- [ ] The test operator has a printed abort card with the exact
      sequence: (1) hit E-stop, (2) `killall -9 gnss-sim`, (3) unplug
      the SDR USB cable, (4) log the abort reason in
      `findings/gnss/<utc>-tx/abort.md`.

## Post-flight

- [ ] All I/Q recordings are stored inside
      `findings/gnss/<utc>-tx/iq/`. Never upload GNSS captures to any
      external service — multipath + geometry fingerprints the fixture.
- [ ] Chamber licence log entry filled with (a) UTC start, (b) UTC
      stop, (c) EIRP setpoint, (d) receiver-under-test model + serial.
- [ ] `plan/roe.json` timestamped signature-of-completion added to
      the engagement audit trail.

**If any box remains unchecked, do not proceed. Abort and file
`outcome=blocked` with the failing item cited.**
