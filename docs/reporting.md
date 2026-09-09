# Fleet reporting

A draft schema for the optional daily report the integration can send to a
statistics backend. Nothing here is implemented yet.

## Principles

**Opt in, off by default.** A switch in the options flow, plus a button that
renders the exact payload that would be sent, so the decision is informed.

**Pseudonymous.** One `install_id`, a UUID4 generated on first enable and kept
in the integration's store. Not derived from the serial, the MAC, the host or
anything else. Deleting and re-adding the integration produces a new id, and
that is the correct behaviour.

**Daily batch, never a stream.** One POST per day over HTTPS. The unit tolerates
a single Modbus session, so the report must read the coordinator's existing
data and never open a connection of its own.

**Aggregates, never time series.** Room temperature sampled every fifteen
seconds is an occupancy sensor. A daily min, mean and max is not.

**Absent is null, not zero.** Register 62001 reads the sentinel 9999 when the
flow sensor is missing. Coercing that to 0 would poison every fleet average it
touches. `SENTINELS` in `const.py` is filtered before anything is aggregated,
and the count of rejects is reported so a unit that starts throwing them is
visible.

## Envelope

```jsonc
{
  "schema": 1,
  "install_id": "9f1c…",              // UUID4, stable per install
  "sent_at": "2026-09-08T02:00:00Z",
  "period": { "start": "2026-09-07T00:00:00Z", "end": "2026-09-08T00:00:00Z" }
}
```

`schema` is bumped on any breaking change. The backend rejects unknown majors
rather than guessing.

## site

Coarse enough that it identifies a climate, not a household.

```jsonc
"site": {
  "country": "PT",                    // hass.config.country
  "postcode_prefix": "2510",          // 3 to 4 digits; PT 7-digit codes are
                                      // house-precise and must be truncated
  "elevation_m": 40,                  // rounded to 10 m
  "climate": {
    "outdoor_mean_c": 21.8,           // 62000
    "outdoor_min_c": 17.9,
    "outdoor_max_c": 28.4,
    "hdd_17": 0.0,                    // heating degree days, base 17
    "cdd_22": 3.4
  }
}
```

Alternative to the postcode: `hass.config.latitude` and `longitude` rounded to
one decimal, roughly 11 km. Either is enough for degree days and climate zone.

## unit

```jsonc
"unit": {
  "model": "EcoZenith i550 Pro",      // from display discovery
  "heat_pump": "EcoAir 720M",
  "display_sw": "20140214",
  "hp_module_sw": "20120503",
  "commissioned": "2019-04",          // year-month only, user-entered:
                                      // no register carries it
  "serial_hash": "3a9f…"              // optional, sha256(salt + serial)[:16]
}
```

## integration

What is actually running in the field, which no single install can tell you.

```jsonc
"integration": {
  "version": "0.1.0",
  "ha_version": "2026.9.1",
  "transports": ["modbus", "display"],
  "slow_pages": ["heatpump", "operation"],
  "poll_interval_s": 15,
  "slow_interval_s": 1800
}
```

## settings

How the installation is tuned. A snapshot at period end, read only from the
61500 block. Fleet-wide this shows how units are actually set up, which is
unusually hard to learn any other way.

```jsonc
"settings": {
  "room_setpoint_c": 18.5,            // 61509
  "curve_slope": 5.0,                 // 61513
  "curve_adjust": 0.0,                // 61517
  "heating_mode": "auto",             // 61542
  "dhw_mode": "normal",               // 61500
  "max_rps": 120.0,                   // 61572
  "max_immersion_upper_kw": 6.0,      // 61591
  "max_immersion_lower_kw": 9.0,      // 61590
  "sg_mode": "none"                   // 62301
}
```

## energy

Period deltas from the `total_increasing` counters, with the absolute reading
alongside so a counter reset is detectable rather than reported as a huge day.

```jsonc
"energy": {
  "compressor_kwh": 12.4,             // Δ 62341
  "compressor_kwh_total": 2025.0,
  "immersion_kwh": 0.0,               // Δ 62191
  "immersion_kwh_total": 912.0,
  "immersion_share": 0.0,             // the single best tuning indicator
  "delivered_kwh": 41.2,              // display only; null without it
  "cop": 3.32,                        // delivered / (compressor + immersion)
  "compressor_hours": 6.2,            // Δ 62214
  "kwh_per_running_hour": 2.0,
  "counter_reset": false
}
```

Annual COP is **not** computed here. The backend has the daily rows and can
roll a trailing 365 days properly, including partial coverage. A number the
integration computes from an incomplete local history would only mislead.

## operation

```jsonc
"operation": {
  "compressor_starts": 7,             // status transitions, 62017
  "runtime_fraction": 0.26,
  "rps_histogram": { "0": 1240, "30": 210, "60": 90, "90": 12 },  // 62193
  "defrosts": 0,                      // rising edges on 62137
  "degree_minutes": { "min": -420, "mean": -85, "max": 0 },       // 62167
  "dhw_capacity_min_pct": 62          // 62279
}
```

Starts per day and the speed histogram together say whether the unit is
short cycling or oversized, which is the best available proxy for compressor
wear.

## curves

The part a fleet can answer and one house cannot. Binned, so a bin with too few
samples is simply absent rather than noisy.

```jsonc
"curves": {
  "cop_by_outdoor_c":      { "5": 2.9, "10": 3.4, "15": 3.9 },
  "defrosts_by_outdoor_c": { "-5": 4, "0": 6, "5": 2 },
  "delta_t_by_rps":        { "30": 5.1, "60": 6.8 }
}
```

Bins are 5 °C wide and keyed by the lower edge. Null where the day never
reached that bin.

## health

Slow drift here is early fault detection, and none of it says anything about
the household.

```jsonc
"health": {
  "high_pressure_bar": { "mean": 24.1, "p95": 28.0 },   // 62067
  "low_pressure_bar":  { "mean": 6.2,  "p05": 4.9 },    // 62077
  "delta_t_k":         { "mean": 5.4 },                 // 62037 − 62027
  "superheat_k":       { "mean": 6.1 },                 // display
  "alarms": [{ "code": "sensor_outdoor", "count": 1 }],
  "sentinel_rejects": 3,
  "modbus_read_failures": 0,
  "display_cycles_skipped": 2                           // panel was in use
}
```

## comfort

Aggregated only. This is the section to leave out entirely if in doubt.

```jsonc
"comfort": {
  "room_temp_c": { "min": 23.9, "mean": 24.3, "max": 24.6 }   // 62203
}
```

## Deliberately excluded

- Any time series, at any resolution.
- Entity ids. They carry room names, occupant names and vehicle models.
- Raw display widgets or screen text. These can contain the installer's name
  or the site designation.
- Extra hot water events, 61503. A shower schedule is occupancy data. If it is
  ever wanted, a monthly count is the most that should leave the house.
- The serial in clear. Hash it if deduplication is needed at all.
- An exact commissioning date. Year and month is plenty.
