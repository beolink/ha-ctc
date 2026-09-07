# CTC EcoZenith for Home Assistant

Local integration for CTC heat pumps. It talks straight to the unit on your own
network and never touches myUplink or any other cloud.

Tested against a CTC EcoZenith i255 driving an EcoAir 720M, and a CTC EcoZenith
i550 Pro. It should also fit an i250, i350, i360, i555 Pro and EcoLogic, since
they share CTC's BMS register map.

## What it reads

The integration uses two transports at once, because neither alone gives the
whole picture.

**Modbus TCP, port 502.** The documented BMS interface. Around fifty readings,
polled continuously, with no side effects: outdoor and room temperature, flow
and return, heat pump in and out, hot gas and suction gas, high and low
pressure, brine, compressor speed, immersion heater power, phase currents,
degree minutes, energy counters and the status enumerations. This is also the
only safe way to control the unit.

**The display's web interface, port 80.** CTC's own screen mirror, the same
thing myUplink proxies for its remote view. It carries values the Modbus map
simply does not have. The most useful is delivered heat in kilowatts alongside
supplied power, which gives a real coefficient of performance, plus the
expansion valve position, superheat, evaporation and condensation in bar, and
the inverter's own voltages and currents.

## The catch with the display, and what the integration does about it

Only the page the panel is currently showing is kept up to date. Every other
screen returns a frozen snapshot from the last time it was rendered, and asking
for another page does not refresh it. Reading a different page means navigating
there, which moves the physical panel in your plant room.

So the display is treated as a supplement, not the base:

- You choose which pages are worth the trip during setup.
- They are polled on a slow interval, thirty minutes by default.
- The panel is put back where it was afterwards.
- If the panel is not where the integration left it, somebody is standing at it,
  and that cycle is skipped.

There is no second session to escape this with. The `/click2/` and `/scroll2/`
endpoints exist in the display's own JavaScript but the firmware answers 400 to
every form of them, on both models tested.

## Setup

1. On the panel, go to Installer, Define, Remote control and set **Ethernet** to
   **Modbus TCP**. The port row only appears once that is done. Note that this
   is reported to be mutually exclusive with the cloud connection.
2. Add the integration. It sweeps your local network and identifies CTC displays
   by asking each host for `/settings/name`. If yours is on another subnet, or
   the sweep finds nothing, type the address instead.
3. Tick the display pages you want harvested. The menu is read from the unit
   itself, so the list matches your model and your installed options, in your
   own language.

Control entities are off by default. Turn them on under the integration's
options if you want them.

## Control

Control writes only to CTC's volatile 1000 block: maximum compressor speed,
immersion heater limits, room and hot water setpoints, hot water and price mode,
zone mode. Those registers are not stored in EEPROM, so they can be written as
often as needed, and the controller forgets them roughly five minutes after the
last write. That expiry is the safety net. If Home Assistant stops, the heat
pump quietly returns to its own settings.

The stored settings in the 61500 block are exposed read only and never written.
CTC states plainly that the number of write cycles there is limited and that
frequent writing can destroy the controller.

## Known limits

- **One Modbus master.** The controller accepts a second TCP connection and then
  resets it as soon as that client sends anything, which looks exactly like the
  unit being offline. Do not point a second tool at it while this is running.
  That includes a `modbus:` block in `configuration.yaml` pointing at the same
  unit, which has to be removed before this integration can connect.
- **CTC sets the pace.** The controller cannot pipeline and documents an update
  rate of one second, so requests are serialised, spaced out, and capped at a
  hundred registers each. It also needs a moment after the socket opens before
  it answers, so the first poll after a reconnect is delayed deliberately.
- **The web server drops connections above roughly five in flight.** The client
  keeps three.
- **Absent hardware still answers.** The controller replies for ten heat pumps
  and four heating systems whatever is actually installed, with plausible
  numbers. Readings marked as missing use CTC's own markers, plus or minus 9999
  and 10000 and 32767, which are filtered out.
- **The web interface is undocumented.** A firmware update can change it. Modbus
  is documented and will keep working.

## Anonymous statistics

The integration sends one report per day to <https://stats.rnet.se>: which
version of the integration you run, your Home Assistant version and
installation type, the country you have set in Home Assistant itself, an
approximate position rounded to about 11 km, how many entities the integration
created, which transports are in use, whether control is enabled, how many
display pages are harvested and how many register reads failed.

It never sends a name, an address, an exact position, a serial number, an entity
name, a page name or a single measurement from the house, and your IP address is
not stored or used to guess where you are. The position is rounded inside your
own installation before anything is sent, and rounded again on the server, so a
finer value does not exist in the database. Reports are stored per date, never per
time of day, so they cannot show when anyone is home. What the backend accepts
is a closed list with a pattern per field, so free text cannot be stored even
by mistake. The numbers are public at <https://stats.rnet.se>.

The point is to know which versions are actually in the field, which parts are
worth maintaining and whether something is failing on units other than mine.

To opt out: *Settings, Devices and services, CTC EcoZenith, Configure, Send
anonymous usage statistics.* Switching it off also erases what has already been
sent about your installation. The full list of fields and the reasoning:
<https://stats.rnet.se/integritet>. The code that builds the report is
`stats_extra.py`, and the client that sends it is `stats.py`.

## Licence

Apache 2.0.
