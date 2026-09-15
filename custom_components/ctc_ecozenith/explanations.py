"""What each value on the CTC EcoZenith page means, and where it comes from.

Shown on the page when a name is hovered or tapped, the way the NIBE page explains
its registers. Free of Home Assistant imports, like dashboard_views.py, so every
explanation can be checked without an installation (tests/test_dashboard.py).

The texts rest on two sources. What a register is comes from CTC's BMS manual
(162 600 16), as const.py records it, together with what testing on an i255 and an
i550 Pro showed. What a value means to the person reading it comes from CTC's own
manual for the EcoZenith i255, chapter 11.7, which explains the operation data rows
the display shows. Where neither source settles what a value is, the text says so
rather than guessing.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .const import CONTROL_NUMBERS, CONTROL_SELECTS, MODBUS_SENSORS, MODBUS_SETTINGS

#: The Modbus registers the integration reads, by key.
MODBUS: dict[str, str] = {
    "outdoor_temp": "Utetemperaturen från pumpens utegivare. Värmekurvan räknar framledningens börvärde ur den.",
    "dhw_stop_temp": "Temperaturen där pumpen slutar ladda varmvatten, enligt CTC:s registerlista.",
    "dhw_temp_raw": (
        "Dokumenterad som varmvattentemperatur, men står på 0 på en i550 Pro, där Varmvatten "
        "(register 62276) är den givare som lever. Avstängd som standard."
    ),
    "system_status": (
        "Vad styrenheten gör just nu. Värmepump övre: värmepumpen värmer tankens övre del, "
        "alltså varmvatten. Värmepump nedre: den nedre delen, värme till huset. Spetsvärme: bara "
        "elpatronen värmer. Värmepump och spets: båda samtidigt."
    ),
    "radiator_temp": "Temperaturen på radiatorvattnet, där en sådan givare finns.",
    "hs1_flow_setpoint": (
        "Den temperatur vattnet ut till värmesystem 1 ska ha just nu, räknad ur värmekurvan och "
        "utetemperaturen. Står på 0 när värmen är avstängd."
    ),
    "hs1_flow": "Temperaturen på vattnet ut till värmesystem 1.",
    "return_temp": "Temperaturen på vattnet som kommer tillbaka från värmesystemet.",
    "dhw_circulation": (
        "Varmvattencirkulation (VVC) enligt CTC:s registerlista. Innehållet är inte bekräftat på "
        "en verklig anläggning, därför avstängd som standard."
    ),
    "hp1_status": (
        "Värmepumpens eget läge: redo för start, startfördröjd, till värme, till varmvatten, "
        "avfrostning, blockerad, larm eller kommunikationsfel mellan styrenhet och värmepump."
    ),
    "hp1_in": "Temperaturen på vattnet in till värmepumpen.",
    "hp1_out": (
        "Temperaturen på vattnet ut från värmepumpen. Skillnaden mot Värmepump in är hur mycket "
        "värmepumpen värmer vattnet."
    ),
    "hp1_discharge": "Hetgas: köldmediets temperatur när det lämnar kompressorn.",
    "hp1_suction": "Suggas: köldmediets temperatur när det går in i kompressorn.",
    "hp1_high_pressure": (
        "Trycket på köldkretsens varma sida, där värmen lämnas till vattnet. Står kompressorn "
        "stilla jämnar trycken ut sig."
    ),
    "hp1_low_pressure": (
        "Trycket på köldkretsens kalla sida, där värmen hämtas in. Står kompressorn stilla jämnar "
        "trycken ut sig."
    ),
    "hp1_brine_in": (
        "Köldbärarens temperatur in till värmepumpen, för berg-, jord- och sjövärme. En "
        "luft/vattenpump har ingen köldbärare, och på en EcoAir 720M följer talet utetemperaturen."
    ),
    "hp1_brine_out": (
        "Köldbärarens temperatur ut från värmepumpen, för berg-, jord- och sjövärme. En "
        "luft/vattenpump har ingen köldbärare, och på en EcoAir 720M följer talet utetemperaturen."
    ),
    "hp1_charge_pump": "Laddpumpens flöde i procent. Laddpumpen flyttar vattnet mellan värmepumpen och tanken.",
    "hp1_brine_pump": "Brinepumpens hastighet i procent, för berg-, jord- och sjövärme.",
    "hp1_fan": "Fläktens hastighet i procent, för luft/vattenpumpar.",
    "hp1_defrost_timer": (
        "Tid kvar innan värmepumpen tillåts avfrosta. Avfrostningen startar först när förångaren "
        "dessutom är kall nog."
    ),
    "hp1_outdoor_temp": "Utetemperaturen mätt vid värmepumpen.",
    "degree_minutes": (
        "Värmeunderskottet, räknat som skillnaden mellan framledningens börvärde och verkliga "
        "temperatur summerad över tiden. Ju mer negativt, desto mer värme saknas, och kompressorn "
        "startar när underskottet når sin startgräns."
    ),
    "immersion_upper_kw": "Effekten den övre elpatronen ger just nu.",
    "immersion_lower_kw": "Effekten den nedre elpatronen ger just nu.",
    "current_l1": (
        "Husets strömuttag på fas L1, mätt med strömkännare på inkommande ledningar. Blir strömmen "
        "högre än huvudsäkringen kopplar pumpen ner elpatronen för att skydda säkringarna."
    ),
    "current_l2": (
        "Husets strömuttag på fas L2, mätt med strömkännare på inkommande ledningar. Blir strömmen "
        "högre än huvudsäkringen kopplar pumpen ner elpatronen för att skydda säkringarna."
    ),
    "current_l3": (
        "Husets strömuttag på fas L3, mätt med strömkännare på inkommande ledningar. Blir strömmen "
        "högre än huvudsäkringen kopplar pumpen ner elpatronen för att skydda säkringarna."
    ),
    "immersion_kwh": "Energin elpatronen har använt sedan start. Samma räknare som Energi el total i displayens historik.",
    "hp1_rps": "Kompressorns varvtal i varv per sekund.",
    "room_temp_1": "Rumstemperaturen från rumsgivaren för värmesystem 1.",
    "room_temp_2": "Rumstemperaturen från rumsgivaren för värmesystem 2.",
    "compressor_hours": "Kompressorns sammanlagda drifttid i timmar.",
    "compressor_hours_24h": "Hur många minuter kompressorn gick under det senaste dygnet.",
    "hs1_status": "Värmesystem 1: Till, Nattsänkning, Semester eller Värme av.",
    "tank_lower_setpoint": "Börvärdet för tankens nedre del.",
    "dhw_lower_temp": "Temperaturen i tankens nedre del.",
    "dhw_temp": "Varmvattnets temperatur.",
    "dhw_capacity": (
        "Varmvattenkapacitet i procent enligt CTC:s registerlista. Visar 0 på båda enheterna som "
        "provats, så vad talet betyder är inte bekräftat."
    ),
    "sg_mode": (
        "Vilket SmartGrid-läge som gäller. Normal: ingen påverkan. Blockering: det som valts i "
        "pumpens meny spärras. Lågpris och Överkapacitet: temperaturerna höjs med de grader som "
        "ställts in där."
    ),
    "control_sw": "Styrenhetens programversion så som CTC rapporterar den. Numret går bara att jämföra mellan enheter av samma modell.",
    "control_sw_year": "Året för styrenhetens programversion.",
    "hp1_power": (
        "Dokumenterad som tillförd effekt per värmepump, men visar 65,5 på en i550 Pro med "
        "stillastående kompressor. Avstängd som standard tills värdet har bekräftats på en pump i drift."
    ),
    "compressor_kwh": (
        "El som kompressorn har förbrukat sedan start. Samma räknare som Tillförd energi totalt i "
        "displayens historik, och nämnaren i värmefaktorn där displayen saknar den."
    ),
    # The stored settings, 61500 block. Read only: CTC warns that the number of
    # writes this memory tolerates is limited.
    "set_dhw_mode": "Varmvattenprogrammet som är inställt i pumpen: Ekonomi för litet varmvattenbehov, Normal, eller Komfort för stort.",
    "set_extra_dhw": "Hur lång tid Extra varmvatten har kvar.",
    "set_room_1": "Rumstemperaturen som är inställd för värmesystem 1 i pumpens meny.",
    "set_slope_1": (
        "Värmekurvans lutning: hur mycket varmare vattnet till huset blir när det blir kallare ute. "
        "Är det för kallt inne när det är under noll ute, ökas lutningen ett par grader."
    ),
    "set_adjust_1": (
        "Värmekurvans justering, som flyttar hela kurvan uppåt eller nedåt. Är det för kallt inne "
        "när det är över noll ute, ökas justeringen ett par grader."
    ),
    "set_hp1_blocked": "Om kompressorn är tillåten eller spärrad i pumpens meny.",
    "set_heating_mode_1": (
        "Värmeläget för värmesystem 1. Auto: värmen stängs av och slås på efter utetemperaturen. "
        "Till och Från: alltid på eller alltid av."
    ),
    "set_max_rps_1": "Högsta kompressorvarvtal som är inställt i pumpen.",
    "set_max_immersion_lower": "Högsta effekt den nedre elpatronen får ge, enligt pumpens inställning.",
    "set_max_immersion_upper": "Högsta effekt den övre elpatronen får ge, enligt pumpens inställning.",
}

_VOLATILE = (
    " Skrivs till ett flyktigt register som pumpen glömmer cirka fem minuter efter sista "
    "skrivningen, så Home Assistant skriver om det varje minut så länge styrningen gäller."
)

#: The control registers, by key.
CONTROL: dict[str, str] = {
    "ctl_room_setpoint_1": "Önskad rumstemperatur för värmesystem 1. Utan styrning visas pumpens egen inställning." + _VOLATILE,
    "ctl_zone_mode_1": "Tvingar värmesystem 1 till Av, Värme, Kyla, Auto eller På. Släpp styrningen lämnar tillbaka till pumpen." + _VOLATILE,
    "ctl_dhw_mode": (
        "Varmvattenprogram: Ekonomi för litet behov, Normal, Komfort för stort behov, eller Manuell. "
        "Släpp styrningen lämnar tillbaka till pumpens eget program."
    ) + _VOLATILE,
    "ctl_dhw_setpoint": (
        "Önskad varmvattentemperatur. Pumpen har inget register som visar sin egen inställning, "
        "därför står värdet som okänt tills Home Assistant har skrivit det."
    ) + _VOLATILE,
    "ctl_extra_dhw": "Startar Extra varmvatten i så här många timmar. Utan styrning visas tiden som är kvar." + _VOLATILE,
    "ctl_price_mode": (
        "Talar om vilken elpriskategori som gäller: låg, normal eller hög. Vad pumpen gör vid "
        "respektive pris ställs in i dess meny."
    ) + _VOLATILE,
    "ctl_max_rps": "Begränsar kompressorns högsta varvtal. Utan styrning visas pumpens egen inställning." + _VOLATILE,
    "ctl_immersion_lower": "Begränsar hur mycket effekt den nedre elpatronen får ge. Utan styrning visas pumpens egen inställning." + _VOLATILE,
    "ctl_immersion_upper": "Begränsar hur mycket effekt den övre elpatronen får ge. Utan styrning visas pumpens egen inställning." + _VOLATILE,
    "release_control": (
        "Släpper all styrning från Home Assistant på en gång. Inget skrivs: Home Assistant slutar "
        "skriva, och pumpen går tillbaka till sina egna inställningar inom cirka fem minuter."
    ),
}

#: Worked out by the integration rather than read.
DERIVED: dict[str, str] = {
    "compressor_running": "Till när värmepumpens status är till värme, till kyla eller till varmvatten.",
    "defrosting": "Till medan värmepumpen avfrostar. Gäller luft/vattenpumpar.",
    "alarm": "Till när värmepumpens status är av på grund av larm. Larm i resten av anläggningen syns i pumpens meny.",
    "blocked": "Till när värmepumpens status är av och blockerad.",
    "immersion_active": "Till när den nedre elpatronen ger effekt.",
    "smartgrid_active": "Till när SmartGrid-läget är något annat än Normal.",
    "cop_day": (
        "Värmefaktor senaste dygnet: avgiven värme delat med tillförd el, ur två avläsningar av "
        "energiräknarna 20 till 30 timmar isär. Visas när minst 3 kWh har förbrukats."
    ),
    "cop_year": (
        "Värmefaktor för ett rullande år, ur energiräknarna mot en egen avläsning 365 till 380 "
        "dagar gammal. Visas först när integrationen har ett års egna avläsningar."
    ),
    "cop_first_year": (
        "Värmefaktor för första året efter driftstarten. Driftstarten räknas ur hur länge pumpen "
        "har varit spänningssatt, och värdet sparas för gott när året har gått."
    ),
    "cop_lifetime": (
        "Värmefaktor över hela livslängden: all avgiven värme delat med all tillförd el sedan "
        "driftstarten. Visas när minst 50 kWh har förbrukats."
    ),
}

#: What the display says about the unit itself.
IDENTITY: dict[str, str] = {
    "hp_model": "Värmepumpens modell, som displayen visar den.",
    "made": "Tillverkningsår och vecka, lästa ur serienumrets andra grupp om fyra siffror.",
    "serial": "Serienumret: tre grupper om fyra siffror för produkt, tillverkningsår och vecka, och löpnummer.",
    "display_fw": "Displayens programversion, skriven som ett datum.",
    "hp_fw": "Programversionen i värmepumpens styrkort, skriven som ett datum.",
    "bootloader": "Displayens bootloaderversion.",
}

_REGISTERS = {d.key: d.address for d in MODBUS_SENSORS + MODBUS_SETTINGS}
_CONTROL_REGISTERS = {r.key: r.address for r in CONTROL_NUMBERS + CONTROL_SELECTS}
_SETTINGS = {d.key for d in MODBUS_SETTINGS}

#: Display rows by the start of their label, casefolded, in the panel's Swedish
#: and a few in English. Longest match wins, so "inverter motoreffekt" is not
#: taken for "inverter". Numbers after a label are stripped first.
_DISPLAY: tuple[tuple[str, str], ...] = (
    ("vp in/ut", "Vattnets temperatur in till och ut från värmepumpen. Nummer 1 är in, nummer 2 är ut."),
    ("brine in/ut", "Brinens temperatur in till och ut från värmepumpen. Nummer 1 är in, nummer 2 är ut."),
    ("utetemperatur", "Utetemperaturen, som den här delen av anläggningen mäter den."),
    ("givare förångare", "Temperaturgivare vid förångaren, där värmepumpen hämtar värmen."),
    ("givare 2 förångare", "Den andra temperaturgivaren vid förångaren, där värmepumpen hämtar värmen."),
    ("hetgas/suggas", "Köldmediets temperaturer runt kompressorn: hetgas efter den och suggas före den. Numret anger vilket av radens tal på panelen."),
    ("ac choke", "Temperaturen vid invertens drossel (AC choke)."),
    ("inverter motoreffekt", "Effekten invertern driver kompressormotorn med just nu."),
    ("inverter dc-bus", "Likspänningen i invertens mellanled."),
    ("inverter matningsspänning", "Spänningen som matar invertern."),
    ("inverter motorspänning", "Spänningen invertern lägger på kompressormotorn."),
    ("inverter motorström", "Strömmen invertern driver kompressormotorn med."),
    ("inverter", "Temperaturen i invertern som driver kompressorn."),
    ("kompressortemp", "Kompressorns temperatur."),
    ("kompressorvärmare", "Om kompressorvärmaren är på."),
    ("kompressor", "Om kompressorn går. R efter varvtalet betyder reducerat läge, till exempel tyst läge."),
    ("vätskerör", "Temperaturen i vätskeröret, där köldmediet lämnar kondensorn i flytande form."),
    ("expansionsventil", "Hur mycket den elektroniska expansionsventilen är öppen. Den styr flödet av köldmedium till förångaren."),
    ("flöde", "Flödet i laddkretsen mellan värmepumpen och tanken."),
    ("förångning", "Förångningens temperatur och tryck, köldkretsens kalla sida."),
    ("kondensering", "Kondenseringens temperatur och tryck, köldkretsens varma sida."),
    ("överhettning", "Hur många grader varmare suggasen är än förångningen. Överhettningen ser till att bara gas når kompressorn."),
    ("laddpump", "Laddpumpens drift och flöde i procent."),
    ("brinepump", "Brinepumpens drift och hastighet i procent."),
    ("fläkt", "Fläktens drift och hastighet i procent."),
    ("timer avfrostning", "Tid kvar innan värmepumpen tillåts avfrosta. Avfrostningen startar först när förångaren dessutom är kall nog."),
    ("ström l1/l2/l3", "Husets strömuttag per fas, mätt med strömkännare på inkommande ledningar."),
    ("ström", "Strömmen över kompressorn."),
    ("programversion vp-styrkort", "Värmepumpens programversion, skriven som ett datum."),
    ("modell", "Värmepumpens modell."),
    ("avgiven värme totalt", "All värme värmepumpen har levererat sedan driftstarten. Täljaren i värmefaktorn."),
    ("avgiven värme/30", "Värme levererad de senaste 30 dagarna."),
    ("avgiven värme", "Värmeeffekten värmepumpen lämnar just nu, enligt dess egen beräkning."),
    ("avgiven energi/24", "Värme levererad det senaste dygnet."),
    ("avgiven energi", "All värme värmepumpen har levererat sedan driftstarten, i den äldre displayprogramvarans räknare."),
    ("avgiven kyla totalt", "All kyla värmepumpen har levererat sedan driftstarten."),
    ("avgiven kyla/30", "Kyla levererad de senaste 30 dagarna."),
    ("avgiven kyla", "Kyleffekten värmepumpen lämnar just nu."),
    ("tillförd effekt", "Den elektriska effekt som går till värmepumpen just nu. Med avgiven värme ger den värmefaktorn för stunden."),
    ("tillförd energi totalt", "All el värmepumpen har förbrukat sedan driftstarten. Nämnaren i värmefaktorn, och samma räknare som Kompressorenergi."),
    ("tillförd energi/30", "El som värmepumpen har förbrukat de senaste 30 dagarna."),
    ("energi el total", "Hur mycket spetsvärme elpatronen har använt sedan start."),
    ("energi el/30", "Spetsvärme elpatronen har använt de senaste 30 dagarna."),
    ("emxxx va", "Skenbar effekt från en extern energimätare, om en sådan är ansluten."),
    ("emxxx vln", "Spänning mellan fas och nolla från en extern energimätare, om en sådan är ansluten."),
    ("emxxx w", "Effekt från en extern energimätare, om en sådan är ansluten."),
    ("emxxx", "Energi från en extern energimätare, om en sådan är ansluten."),
    ("högsta framledning", "Den högsta temperatur som har levererats till värmesystemet."),
    ("total drifttid", "Hur länge produkten har varit spänningssatt, i timmar. Ur den räknas driftstarten för första årets värmefaktor."),
    ("drifttid total", "Kompressorns sammanlagda drifttid i timmar."),
    ("drift /24", "Kompressorns drifttid under förra dygnet."),
    ("antal starter /24", "Antal kompressorstarter det senaste dygnet."),
    ("antal starter", "Antal kompressorstarter sedan start."),
    ("kritiska larm", "Räknare för kritiska larm som värmepumpen har gett."),
    ("medeltemperatur ute", "Medelvärdet av utetemperaturen de senaste 30 dagarna."),
    ("tank övre", "Temperaturen i tankens övre del. Inom parentes på panelen står börvärdet."),
    ("tank nedre", "Temperaturen i tankens nedre del. Inom parentes på panelen står börvärdet."),
    ("extern vv-tank", "Temperaturen i en extern varmvattentank."),
    ("vv-tank", "Temperaturen i varmvattentanken."),
    ("ext. bufferttank", "Temperaturen i den externa bufferttanken."),
    ("laddstart", "Temperaturen där laddningen av bufferttanken startar."),
    ("tidsräknare kyltank", "Tidsräknare för kyltanken."),
    ("kyltank", "Temperaturen i kyltanken."),
    ("elpatron", "Effekten elpatronen ger just nu."),
    ("eleffekt", "Tillskottseffekten på elpatronerna, nedre och övre."),
    ("gradminutkyla", "Gradminuter för kyla: underskottet mot kylbehovet summerat över tiden."),
    ("gradminut", "Värmeunderskottet som gradminuter. Ju mer negativt, desto mer värme saknas, och kompressorn startar vid sin startgräns."),
    ("fördröjning shuntventil", "Tid kvar innan tillsatsvärme får användas via shunten, så att elpatronen inte används i onödan vid ett tillfälligt temperaturfall."),
    ("shuntfördröjning", "Tid kvar innan tillsatsvärme får användas via shunten, så att elpatronen inte används i onödan vid ett tillfälligt temperaturfall."),
    ("fördröjning spets", "Tid kvar innan spetsvärmen får starta."),
    ("radiatorpump", "Om radiatorpumpen går."),
    ("shuntventil", "Om shuntventilen öppnar eller stänger värmen ut till värmesystemet."),
    ("smartgrid", "Vilket SmartGrid-läge som gäller för den här delen."),
    ("framledning", "Temperaturen ut till värmesystemet. Inom parentes på panelen står börvärdet."),
    ("returledning", "Temperaturen tillbaka från värmesystemet."),
    ("rumstemperatur", "Rumstemperaturen från rumsgivaren. Inom parentes på panelen står börvärdet."),
    ("extra varmvatten", "Om Extra varmvatten är aktivt."),
    ("elprisläge", "Vilken elpriskategori som gäller just nu: hög, medium eller låg."),
    ("elpris", "Aktuellt elpris i lokal valuta."),
    ("status", "Driftläget för den här delen av anläggningen, som panelen visar det."),
    ("läge", "Vilket program som är aktivt."),
    ("energy output total", "All värme värmepumpen har levererat sedan driftstarten. Täljaren i värmefaktorn."),
    ("energy consumption total", "All el värmepumpen har förbrukat sedan driftstarten. Nämnaren i värmefaktorn."),
    ("energy output", "Värmeeffekten värmepumpen lämnar just nu, enligt dess egen beräkning."),
    ("energy add", "Den elektriska effekt som går till värmepumpen just nu."),
    ("total operation time", "Drifttid i timmar: hur länge produkten har varit spänningssatt, eller kompressorns drifttid."),
)
_DISPLAY_SORTED = sorted(_DISPLAY, key=lambda item: len(item[0]), reverse=True)
_NUMBER_SUFFIX = re.compile(r"\s+\d+$")

_UNNAMED = (
    "Ett tal från displayens sida utan en egen rubrik på panelen, därför namngivet efter sidan. "
    "Vad det visar framgår av panelen."
)
_GENERIC = "Ett värde som displayen visar på den här sidan, under samma namn."


def display_explanation(label: str | None, page_title: str | None = None) -> str:
    """The explanation for a display row, from the label the panel printed."""
    text = _NUMBER_SUFFIX.sub("", str(label or "").strip()).casefold()
    title = str(page_title or "").strip().casefold()
    if not text or text == title or text.startswith("värde"):
        return _UNNAMED
    for prefix, explanation in _DISPLAY_SORTED:
        if text.startswith(prefix):
            return explanation
    return _GENERIC


def explain(key: str) -> str | None:
    """The explanation for anything but a display row, or None for an unknown key."""
    for table in (MODBUS, CONTROL, DERIVED, IDENTITY):
        if key in table:
            return table[key]
    return None


def source(key: str, pages: list[Mapping[str, Any]] | None = None, interval: int | None = None) -> str | None:
    """Where a value comes from, in a few words."""
    if key in _CONTROL_REGISTERS:
        return f"Modbus-register {_CONTROL_REGISTERS[key]}, flyktigt"
    if key in _REGISTERS:
        address = _REGISTERS[key]
        if key in _SETTINGS:
            return f"Modbus-register {address}, lagrad inställning som bara läses"
        return f"Modbus-register {address}"
    if key == "release_control":
        return "Knapp i Home Assistant"
    if key in ("compressor_running", "defrosting", "alarm", "blocked"):
        return "Räknas fram ur Värmepump status, Modbus-register 62017"
    if key == "immersion_active":
        return "Räknas fram ur Elpatron nedre, Modbus-register 62169"
    if key == "smartgrid_active":
        return "Räknas fram ur SmartGrid-läge, Modbus-register 62301"
    if key.startswith("cop_"):
        return "Räknas ur energiräknarna"
    if key == "made":
        return "Räknas ur serienumret"
    if key in IDENTITY:
        return "Displayens systeminformation"
    for page in pages or []:
        if any(value.get("key") == key for value in page.get("values") or []):
            every = f", hämtas var {max(1, round(interval / 60))}:e minut" if interval else ""
            return f"Displaysidan {page.get('title')}{every}"
    return None
