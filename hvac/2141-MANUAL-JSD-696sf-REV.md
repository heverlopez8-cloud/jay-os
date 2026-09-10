# MANUAL J / S / D — REVISED FOR 696 ft²
**Job 2141 — JAY, PROFESSIONAL CAD DESIGN — "PLAN SINGLE UNIT", Phoenix, AZ**
Basis document: `MANUAL_J_S_D.pdf` (Right-Suite Universal 2019 19.0.09 RSU23025, run 2019-Aug-19, by Evan)
Revision basis: floor areas corrected per client (all six rooms client-directed). Front door faces E. Calc = MJ8.

> **TWO SCHEMES IN THIS SET.** §1–§10 are **SCHEME A — 3 ton** (RJPL-A036JK000), which passes
> Manual S at 103% and needs no duct changes. **§11 is SCHEME B — 3.5 ton** (RJPL-A042JK000), as
> directed, and is what the issued PDF renders. Scheme B lands at **118% of load — 3 points over the
> 115% Manual S limit** — and **requires nine duct runs to be upsized**. Read §11 before issuing.
>
> **STATUS: ENGINEERING CHECK SET — NOT A SUBMITTAL.** These are hand-verified Manual J8 / S / D
> figures reproducing Right-Suite's method. They are not ACCA-approved software output and carry no
> "Calculations approved by ACCA" certification. Re-enter the corrected areas in Right-Suite and
> re-print for permit. Use this set to verify that run.

---

## 1. AREA CHANGE — WHAT WAS GIVEN vs. WHAT WAS ASSUMED

| Room | OLD ft² | NEW ft² | Δ | Source |
|---|---|---|---|---|
| BEDROOM 1 | 92.5 | **117** | +26.5% | Client-directed |
| BEDROOM 2 | 92.5 | **117** | +26.5% | Client-directed |
| BATH | 43.1 | **52** | +20.6% | Client-directed |
| LAUNDRY | 26.3 | **34** | +29.3% | Client-directed |
| LIVING ROOM | 180.1 | **162** | −10.0% | Client-directed (balance) |
| KITCHEN | 115.6 | **214** | +85.1% | Client-directed |
| **Entire House** | **550.0** | **696** | **+26.5%** | Client-directed |

All six room areas are now client-directed. KITCHEN was given as 214 ft²; LIVING ROOM takes the
balance: 696 − 117 − 117 − 52 − 34 − 214 = **162 ft²**.

This inverts the original plan. The kitchen is now the largest room in the unit, and the living room
**shrank 10%** while the house grew 26.5%. Confirm the two room names are not transposed on the
revised plan — see review comment 11.

### Held constant per instruction ("just changing the areas")
Exposed wall lengths, wall gross/net areas, window schedule, door, orientations, all U-values and
HTMs, construction types, ceiling height (9.0 ft), duct-load fractions, equipment selection, and
duct layout. Volume follows area (696 × 9 = 6,264 ft³), so infiltration scales with volume.

**Engineering caveat:** a room cannot gain 26% of floor area without gaining exposed wall. Holding
walls and glazing fixed is arithmetically consistent but physically optimistic — it understates
envelope load. See §8.

---

## 2. DESIGN CONDITIONS (unchanged)

| | Heating | Cooling |
|---|---|---|
| Outdoor db (°F) | 37 | 108 |
| Outdoor wb (°F) | — | 69.9 |
| Indoor db (°F) | 70 | 75 |
| Design TD (°F) | 33 | 33 |
| Indoor RH (%) | 30 | 45 |
| Moisture difference (gr/lb) | +7.1 | −7.3 |

Phoenix, AZ · Elev 1,112 ft · Lat 33°N · Daily range M · Wind 15.0 / 7.5 mph
Infiltration method: Simplified · Construction quality: Average · Fireplaces: 0

### Construction (unchanged)
| Ty | Construction | U-value | HTM htg | HTM clg |
|---|---|---|---|---|
| W | 12E-0sw | 0.068 | 2.24 | 2.34 |
| D | 11D0 | 0.390 | 12.87 | 16.98 |
| G | PHX MINS | 0.400 | 13.20 | 37.16 |
| G | 1D-c2ovd | 0.570 | 18.81 | 72.46 |
| C | 18A-38zd | 0.029 | 0.96 | 1.13 |
| F | 19C-19cscp | 0.049 | 0.56 | 0.56 |

Duct load fractions carried forward: **51.3% heating / 38.0% cooling** of room subtotal.

---

## 3. LOAD SHORT FORM — REVISED

| ROOM NAME | Area (ft²) | Htg load (Btuh) | Clg load (Btuh) | Htg AVF (cfm) | Clg AVF (cfm) |
|---|---:|---:|---:|---:|---:|
| BEDROOM 1 | 117 | 1,955 | 2,654 | 159 | 119 |
| BEDROOM 2 | 117 | 1,343 | 2,247 | 109 | 101 |
| LAUNDRY | 34 | 803 | 1,216 | 65 | 55 |
| BATH | 52 | 501 | 370 | 41 | 17 |
| LIVING ROOM | 162 | 4,522 | 12,993 | 367 | 584 |
| KITCHEN | 214 | 2,705 | 7,198 | 220 | 324 |
| **Entire House** | **696** | **11,822** | **26,679** | **960** | **1,200** |
| Other equip loads (vent 45 cfm) | | 1,569 | 1,574 | | |
| Equip. @ 1.00 RSM | | | 28,253 | | |
| Latent cooling | | | 984 | | |
| **TOTALS** | **696** | **13,391** | **29,237** | **960** | **1,200** |

Air flow factors: heating **0.081** cfm/Btuh · cooling **0.045** cfm/Btuh

### Change vs. original
| | 550 ft² | 696 ft² | Δ |
|---|---:|---:|---:|
| Equipment heating load | 12,348 | 13,391 | +8.4% |
| Equipment sensible cooling | 27,577 | 28,253 | +2.5% |
| Equipment total cooling | 28,590 | 29,237 | +2.3% |
| ft² per ton (cooling) | 231 | 286 | — |

The load barely moves because in this model glazing and ducts carry ~64% of the cooling load and
neither changed. Only ceiling, floor, infiltration and volume responded to the area correction.

---

## 4. BUILDING ANALYSIS — REVISED

### Heating (total 13,391 Btuh)
| Component | Btuh/ft² | Btuh | % of load |
|---|---:|---:|---:|
| Walls | 2.2 | 1,436 | 10.7 |
| Glazing | 14.6 | 2,830 | 21.1 |
| Doors | 12.9 | 270 | 2.0 |
| Ceilings | 1.0 | 668 | 5.0 |
| Floors | 0.6 | 390 | 2.9 |
| Infiltration | 2.6 | 2,221 | 16.6 |
| Ducts | | 4,007 | 29.9 |
| Piping | | 0 | 0 |
| Humidification | | 0 | 0 |
| Ventilation | | 1,569 | 11.7 |
| **Total** | | **13,391** | **100.0** |

### Cooling (total 28,253 Btuh)
| Component | Btuh/ft² | Btuh | % of load |
|---|---:|---:|---:|
| Walls | 2.3 | 1,499 | 5.3 |
| Glazing (incl. AED excursion) | 54.6 | 10,591 | 37.5 |
| Doors | 17.0 | 357 | 1.3 |
| Ceilings | 1.1 | 786 | 2.8 |
| Floors | 0.6 | 390 | 1.4 |
| Infiltration | 1.4 | 1,168 | 4.1 |
| Ducts | | 7,348 | 26.0 |
| Ventilation | | 1,574 | 5.6 |
| Internal gains | | 4,540 | 16.1 |
| Blower | | 0 | 0 |
| **Total** | | **28,253** | **100.0** |

Latent Cooling Load = **984 Btuh**
Overall U-value ≈ **0.082** Btuh/ft²-°F (was 0.092 — improves only because area grew, not the envelope)

> **WARNING — window to floor area ratio = 27.9%** (was 35.3%). Still exceeds the 25% Right-Suite
> flag. The area correction reduced it but did not clear it.

---

## 5. PROJECT SUMMARY — REVISED

### Heating Summary
| | Btuh |
|---|---:|
| Structure | 7,815 |
| Ducts | 4,007 |
| Central vent (45 cfm) | 1,569 |
| Humidification | 0 |
| Piping | 0 |
| **Equipment load** | **13,391** |

### Sensible Cooling Equipment Load Sizing
| | Btuh |
|---|---:|
| Structure | 19,331 |
| Ducts | 7,348 |
| Central vent (45 cfm) | 1,574 |
| Blower | 0 |
| Use manufacturer's data | y |
| Rate/swing multiplier | 1.00 |
| **Equipment sensible load** | **28,253** |

### Latent Cooling Equipment Load Sizing
| | Btuh |
|---|---:|
| Structure | 1,341 |
| Ducts | −141 |
| Central vent (45 cfm) | −216 |
| **Equipment latent load** | **984** |

**Equipment Total Load (Sen+Lat) = 29,237 Btuh**
**Req. total capacity at 0.70 SHR = 3.4 ton** (was 3.3)

### Infiltration
| | Heating | Cooling |
|---|---:|---:|
| Area (ft²) | 696 | 696 |
| Volume (ft³) | 6,264 | 6,264 |
| Air changes/hour | 0.61 | 0.32 |
| Equiv. AVF (cfm) | 64 | 33 |

ACH held constant: 550 ft² and 696 ft² both fall in the same MJ8 Table 5A floor-area band (<900 ft²).

---

## 6. RIGHT-J WORKSHEET — REVISED, ROOM BY ROOM

Only lines that changed are marked ▲. All wall/glazing/door loads carried over unchanged.

### BEDROOM 1 — 117.0 ft², exposed wall 19.3 ft, 9.0 ft ht
| Line | Component | Area/Perim | Heat | Cool |
|---|---|---:|---:|---:|
| 6 | W 12E-0sw n | 83 / 83 | 187 | 195 |
| | W 12E-0sw e | 90 / 65 | 146 | 152 |
| | G PHX MINS e | 25 | 330 | 929 |
| ▲ | C 18A-38zd | 117 | 112 | 132 |
| ▲ | F 19C-19cscp | 117 | 66 | 66 |
| | c) AED excursion | | | −63 |
| ▲ | **Envelope loss/gain** | | **841** | **1,411** |
| ▲ 12 | a) Infiltration | | 451 | 237 |
| 13 | Appliances/other | | | 275 |
| ▲ 14 | **Subtotal** | | **1,292** | **1,923** |
| ▲ 15 | Duct loads 51% / 38% | | 663 | 731 |
| ▲ | **Total room load** | | **1,955** | **2,654** |
| ▲ | **Air required (cfm)** | | **159** | **119** |

### BEDROOM 2 — 117.0 ft², exposed wall 10.0 ft, 9.0 ft ht
| Line | Component | Area/Perim | Heat | Cool |
|---|---|---:|---:|---:|
| 6 | W 12E-0sw e | 90 / 65 | 146 | 152 |
| | G PHX MINS e | 25 | 330 | 929 |
| ▲ | C 18A-38zd | 117 | 112 | 132 |
| ▲ | F 19C-19cscp | 117 | 66 | 66 |
| | c) AED excursion | | | −49 |
| ▲ | **Envelope loss/gain** | | **654** | **1,230** |
| ▲ 12 | a) Infiltration | | 234 | 123 |
| 13 | Appliances/other | | | 275 |
| ▲ 14 | **Subtotal** | | **888** | **1,628** |
| ▲ 15 | Duct loads 51% / 38% | | 455 | 619 |
| ▲ | **Total room load** | | **1,343** | **2,247** |
| ▲ | **Air required (cfm)** | | **109** | **101** |

### LAUNDRY — 34.0 ft², exposed wall 11.0 ft, 9.0 ft ht
| Line | Component | Area/Perim | Heat | Cool |
|---|---|---:|---:|---:|
| 6 | W 12E-0sw e | 68 / 68 | 151 | 158 |
| | W 12E-0sw s | 32 / 32 | 71 | 74 |
| ▲ | C 18A-38zd | 34 | 33 | 38 |
| ▲ | F 19C-19cscp | 34 | 19 | 19 |
| | c) AED excursion | | | −43 |
| ▲ | **Envelope loss/gain** | | **274** | **246** |
| ▲ 12 | a) Infiltration | | 257 | 135 |
| 13 | Appliances/other | | | 500 |
| ▲ 14 | **Subtotal** | | **531** | **881** |
| ▲ 15 | Duct loads 51% / 38% | | 272 | 335 |
| ▲ | **Total room load** | | **803** | **1,216** |
| ▲ | **Air required (cfm)** | | **65** | **55** |

### BATH — 52.0 ft², exposed wall 5.8 ft, 9.0 ft ht
| Line | Component | Area/Perim | Heat | Cool |
|---|---|---:|---:|---:|
| 6 | W 12E-0sw s | 52 / 52 | 116 | 121 |
| ▲ | C 18A-38zd | 52 | 50 | 59 |
| ▲ | F 19C-19cscp | 52 | 29 | 29 |
| | c) AED excursion | | | −12 |
| ▲ | **Envelope loss/gain** | | **195** | **197** |
| ▲ 12 | a) Infiltration | | 136 | 71 |
| ▲ 14 | **Subtotal** | | **331** | **268** |
| ▲ 15 | Duct loads 51% / 38% | | 170 | 102 |
| ▲ | **Total room load** | | **501** | **370** |
| ▲ | **Air required (cfm)** | | **41** | **17** |

### LIVING ROOM — 162.0 ft², exposed wall 27.5 ft, 9.0 ft ht
| Line | Component | Area/Perim | Heat | Cool |
|---|---|---:|---:|---:|
| 6 | W 12E-0sw n | 97 / 76 | 170 | 177 |
| | D 11D0 n | 21 | 270 | 357 |
| | W 12E-0sw w | 151 / 55 | 123 | 128 |
| | G 1D-c2ovd w | 48 | 903 | 3,478 |
| | G PHX MINS w | 48 | 634 | 1,784 |
| ▲ | C 18A-38zd | 162 | 156 | 183 |
| ▲ | F 19C-19cscp | 162 | 91 | 91 |
| | c) AED excursion | | | +1,519 |
| ▲ | **Envelope loss/gain** | | **2,346** | **7,717** |
| ▲ 12 | a) Infiltration | | 643 | 338 |
| 13 | Occupants @ 230 × 2 | | | 460 |
| | Appliances/other | | | 900 |
| ▲ 14 | **Subtotal** | | **2,989** | **9,415** |
| ▲ 15 | Duct loads 51% / 38% | | 1,533 | 3,578 |
| ▲ | **Total room load** | | **4,522** | **12,993** |
| ▲ | **Air required (cfm)** | | **367** | **584** |

> **Load density 80 Btuh/ft² cooling — 3.6 cfm/ft².** This 162 ft² room takes 584 cfm, 49% of total
> system airflow into 23% of the floor area. See review comment 12.

### KITCHEN — 214.0 ft², exposed wall 21.5 ft, 9.0 ft ht
| Line | Component | Area/Perim | Heat | Cool |
|---|---|---:|---:|---:|
| 6 | W 12E-0sw s | 97 / 97 | 217 | 227 |
| | W 12E-0sw w | 97 / 49 | 109 | 114 |
| | G PHX MINS w | 48 | 634 | 1,784 |
| ▲ | C 18A-38zd | 214 | 205 | 242 |
| ▲ | F 19C-19cscp | 214 | 120 | 120 |
| | c) AED excursion | | | +335 |
| ▲ | **Envelope loss/gain** | | **1,285** | **2,822** |
| ▲ 12 | a) Infiltration | | 503 | 264 |
| 13 | Occupants @ 230 × 1 | | | 230 |
| | Appliances/other | | | 1,900 |
| ▲ 14 | **Subtotal** | | **1,788** | **5,216** |
| ▲ 15 | Duct loads 51% / 38% | | 917 | 1,982 |
| ▲ | **Total room load** | | **2,705** | **7,198** |
| ▲ | **Air required (cfm)** | | **220** | **324** |

**Method note on infiltration:** the original model apportions whole-house infiltration strictly by
exposed wall length (1,755 ÷ 95.0 ft = 18.47 Btuh/ft heating). Verified against every room in the
source. Revised rate: **23.38 Btuh/ft heating, 12.29 Btuh/ft cooling** (whole-house 2,221 / 1,168
Btuh, scaled by volume 6,264/4,950).

---

## 7. MANUAL S COMPLIANCE — REVISED

Equipment retained: **Rheem RJPL-A036JK000 packaged air-source heat pump**, AHRI 1259368,
12.0 EER / 14 SEER / 8 HSPF. 1,200 cfm cooling, 960 cfm heating, 0.50 in H2O ESP.

### Cooling
| | Value |
|---|---|
| Outdoor design DB / WB | 108 °F / 69.9 °F |
| Indoor design DB / RH | 75.0 °F / 45% |
| Sensible gain | 28,253 Btuh |
| Latent gain | 984 Btuh |
| Total gain | 29,237 Btuh |
| Estimated airflow | 1,200 cfm |
| Entering coil DB / WB | ≈79.8 °F / 62.3 °F |

**Manufacturer's performance at actual design conditions (1,200 cfm):**
| | Capacity | % of load | Was |
|---|---:|---:|---:|
| Sensible | 29,147 Btuh | **103%** | 106% |
| Latent | 963 Btuh | **98%** | 95% |
| Total | 30,110 Btuh | **103%** | 105% |
| SHR | 97% | | 97% |

### Heating
| | Value |
|---|---|
| Outdoor / indoor design DB | 37.0 °F / 70.0 °F |
| Heat loss | 13,391 Btuh |
| Entering coil DB | ≈67.0 °F |
| Actual airflow | 960 cfm |
| Output capacity | 28,588 Btuh — **214% of load** (was 232%) |
| Supplemental heat required | **0 Btuh** |
| Capacity balance point | **≈15 °F** (was 13 °F) |

**MEETS ALL REQUIREMENTS OF ACCA MANUAL S.** Total cooling capacity at 103% of load sits comfortably
inside the 90–115% band; sensible at 103% is well placed. Heating oversizing is permitted for a heat
pump sized on the cooling load.

> Balance point is interpolated from the original report's stated 13 °F, because the manufacturer's
> expanded performance table is not in the source PDF. Verify against Rheem data when re-running.

---

## 8. MANUAL D — REVISED

### Static Pressure and Friction Rate (unchanged — equipment airflow did not change)
| Available Static Pressure | Heating | Cooling |
|---|---:|---:|
| External static pressure | 0.50 | 0.50 |
| Coil | 0.14 | 0.14 |
| Heat exchanger | 0 | 0 |
| Supply diffusers | 0.03 | 0.03 |
| Return grilles | 0.03 | 0.03 |
| Filter | 0.08 | 0.08 |
| Humidifier / damper / other | 0 | 0 |
| **Available static pressure** | **0.22** | **0.22** |

Supply / return available pressure: 0.158 / 0.062 in H2O
Total effective length: supply **226 ft**, return **64 ft**
**Friction rate: 0.097 in/100 ft — OK, supply and return**
Fittings — Supply: 4AD=60, 11T=25, 11T=25, 1A=35 (TotalEL=145) · Return: 5D=40, 6M=20 (TotalEL=60)

### Supply Branch Detail — REVISED
| Name | Design (Btuh) | Htg cfm | Clg cfm | FR | Diam (in) | Matl | Duct Ln | Ftg EL | Trunk |
|---|---:|---:|---:|---:|---:|---|---:|---:|---|
| BATH | h 501 | 41 | 17 | 0.100 | 6.0 | VlFx | 12.4 | 145.0 | st3 |
| BEDROOM 1 | h 1,955 | 159 | 119 | 0.097 | 8.0 | VlFx | 17.3 | 145.0 | st1 |
| BEDROOM 2 | h 1,343 | 109 | 101 | 0.102 | 8.0 | VlFx | 9.8 | 145.0 | st1 |
| KITCHEN | c 3,599 | 110 | 162 | 0.156 | 8.0 | VlFx | 6.2 | 95.0 | — |
| KITCHEN-A | c 3,599 | 110 | 162 | 0.158 | 8.0 | VlFx | 4.7 | 95.0 | — |
| LAUNDRY | h 803 | 65 | 55 | 0.099 | 6.0 | VlFx | 14.6 | 145.0 | st3 |
| LIVING ROOM | c 6,497 | 184 | 292 | 0.128 | 10.0 | VlFx | 3.9 | 120.0 | st2 |
| LIVING ROOM-B | c 6,497 | 184 | 292 | 0.122 | 10.0 | VlFx | 9.3 | 120.0 | st2 |

### Supply Trunk Detail — REVISED
| Trunk | Type | Htg cfm | Clg cfm | FR | Velocity (fpm) | Diam (in) | Matl | Trunk |
|---|---|---:|---:|---:|---:|---:|---|---|
| st2 | Peak AVF | 367 | 584 | 0.122 | 546 | 14.0 | VinlFlx | — |
| st3 | Peak AVF | 106 | 72 | 0.099 | 304 | 8.0 | VinlFlx | st1 |
| st1 | Peak AVF | 374 | 292 | 0.097 | 476 | 12.0 | VinlFlx | — |

### Return Branch Detail (unchanged)
| Grille | Htg cfm | Clg cfm | TEL | FR | Velocity | Diam | Matl |
|---|---:|---:|---:|---:|---:|---:|---|
| rb1 | 960 | 1,200 | 63.8 | 0.097 | 679 | 18.0 | VlFx |

**RESULT: NO DUCT RESIZING REQUIRED.** Because the equipment airflow is fixed at 960/1,200 cfm, the
room airflows shift by only a few cfm and every branch, trunk and the return hold their existing
size at the same friction rate. Duct lengths are carried over unchanged — if the plan grew
physically, remeasure the runs and re-check TEL (see §9).

---

## 9. ENGINEER'S REVIEW COMMENTS

Findings on the original set that survive into the revision, in priority order.

**1. AED test still FAILS — must be resolved.**
Maximum hourly glazing load exceeds the average by **52.1%** against a 30% AED limit; AED excursion
**1,687 Btuh** (PFG − 1.3×AFG). Glazing is unchanged, so this does not improve with the area
correction. The house has no adequate exposure diversity — nearly all glass is on the E and W. West
glass in Phoenix at 108 °F is the single worst orientation. Options: relocate glass to N/S, add
exterior shading to the west openings, or accept the excursion and zone the west side separately.

**2. Duct losses of 51% heating / 38% cooling are extreme.**
Ducts alone contribute **4,007 Btuh heating and 7,348 Btuh cooling — 30% and 26% of the total loads.**
That signals uninsulated or lightly insulated flex in an unconditioned Phoenix attic with a high
leakage assumption. Moving the ducts into conditioned space, or going to R-8 with a sealed and
tested (≤4% leakage) system, would cut the equipment load more than any envelope measure on this
project. This is the highest-value item on the list.

**3. Window-to-floor-area ratio 27.9% — still over the 25% flag.**
Improved from 35.3% by the area correction alone, but 194 ft² of glazing on a 696 ft² unit is a lot.
Glazing carries **37.5% of the cooling load** and 21% of heating.

**4. The 1D-c2ovd west window is the worst single component in the building.**
48 ft² at U-0.570, cooling HTM 72.46 → **3,478 Btuh from one window**, 12% of the entire cooling load.
U-0.570 will not meet 2021 IECC Zone 2B (U-0.40 max, SHGC 0.25 max) and is roughly a clear
double-pane with no low-E. Swapping it to the PHX MINS spec (U-0.400) already used elsewhere in the
model would cut roughly 1,700 Btuh of cooling load — about 0.15 ton — for very little cost.

**5. Ventilation margin is now thin.**
45 cfm central vent was carried forward. At 696 ft² with 2 bedrooms, ASHRAE 62.2-2016 requires
0.03 × 696 + 7.5 × 3 = **43.4 cfm.** 45 cfm still complies but with only 1.6 cfm of margin. If the
bedroom count or area moves again, recheck.

**6. Holding walls and glazing constant while growing floor area is optimistic.**
Physically, +26.5% floor area means more exposed wall. If exposed wall scales with the perimeter of
the enlarged plan (roughly +12–13% at constant aspect ratio, i.e. ~107 ft vs. 95 ft), wall and
infiltration loads rise and the cooling load lands nearer **30,000–30,500 Btuh** — which would push
the Rheem 3-ton to about 99–100% of load and eliminate the sizing margin. **Get me the revised
exposed wall lengths and window schedule before this goes to permit.**

**7. Duct lengths were not remeasured.**
TEL of 226 ft supply was carried over. A 26% larger plan almost certainly has longer runs. If TEL
goes to, say, 260 ft, friction rate drops to 0.085 in/100 ft and the 8" branches to BEDROOM 1
(159 cfm) become marginal. Remeasure from the new plan.

**8. Bath supply is nominal.**
41 cfm heating / 17 cfm cooling through a 6" branch. Fine for a bath, but confirm the exhaust fan
and door undercut so the room is not pressurized against the return path.

**9. Source-file housekeeping.**
The PDF carries four incremental save revisions; the job was renamed from "PLAN 2 550" to
"PLAN SINGLE UNIT" and stale page objects from the earlier name remain embedded. The Manual D and
plan sheets still reference `...550.rup`. Re-save clean before issuing.

**11. Verify LIVING ROOM and KITCHEN are not transposed.**
The revised areas make the kitchen (214 ft²) 32% larger than the living room (162 ft²) in a 696 ft²
two-bedroom unit, and shrink the living room 10% below the original 180 ft² while the house grew
26.5%. That is an unusual program. It is also inconsistent with the envelope in the model: the
162 ft² living room carries 27.5 ft of exposed wall, 96 ft² of glazing and the front door, while the
214 ft² kitchen carries 21.5 ft of wall and 48 ft² of glass. If the plan is actually a combined
great-room/kitchen, or the labels swapped, say so and I will rebuild both rooms.

**12. LIVING ROOM airflow is now very dense — check diffuser throw and noise.**
584 cfm cooling into 162 ft² is **3.6 cfm/ft², 49% of total system airflow into 23% of the floor
area**, at a cooling load density of 80 Btuh/ft². The two existing 10" branches will carry it at
292 cfm and 535 fpm each, which is acceptable duct-side, but two outlets at ~292 cfm apiece in a
small room will be loud and will dump. Recommend **three supply outlets** at ~195 cfm, selected for
throw against the west glass, and confirm the return path from this room.

**13. Two-story sheets, single-zone model.**
Plan sheets are labeled "1ST FLOOR" and "2ND STORY", but the load model is a single 9-ft-high zone
with 95 ft of exposed wall — that is one story's worth of wall. If there is genuinely conditioned
second-floor area, it is not in this Manual J and the whole set needs rebuilding.

---

## 10. OPEN ITEMS — NEEDED TO FINALIZE

1. ~~LIVING ROOM and KITCHEN areas~~ — **RESOLVED**: Kitchen 214, Living Room 162 (balance).
   Confirm the labels are not transposed (review comment 11).
2. **Revised exposed wall length per room** and the whole-house total (was 95.0 ft).
3. **Revised window schedule** — area, orientation, overhang per room (was 194 ft², E and W heavy).
4. **Revised room dimensions** (was BR 9.3×10.0, Bath 5.8×7.5, Laundry 3.5×7.5, LR 10.8×16.8, Kitchen 10.8×10.8).
5. **The corrected floor plan itself** — it was referenced but not provided.
6. **Confirm ceiling height stays 9.0 ft** and the unit remains single-story.
7. **Confirm the Rheem RJPL-A036JK000 is still the intended unit** (2019 selection; 14 SEER is below the
   2023 federal minimum of SEER2 14.3 / 15.2 for the South region — this model may no longer be available).


---

# 11. SCHEME B — 3.5 TON RJPL-A042JK000 (AS DIRECTED) — **ISSUED PDF**

Client direction: step the unit up. Landed on **3.5 ton**, not 4 ton. This is the scheme rendered in
`2141-696sf-3.5TON-RJPL-A042JK000.pdf`.

Manual J loads are identical to Scheme A — **changing equipment does not change the building load.**
Only the equipment block, airflows, Manual S check and Manual D change.

## 11.1 Equipment — real Rheem data

Pulled from the Rheem/Ruud RJPL package heat pump catalog (the same document that confirms the
original 3-ton: RJPL-A036JK, AHRI net 36,800 Btuh, 33,600 Btuh heating at 47 °F, 12.0 EER / 14 SEER,
1200 cfm — every figure matches the source report exactly, which validates the extraction).

| | 3 ton (A036JK) | **3.5 ton (A042JK)** | 4 ton (A048JK) |
|---|---:|---:|---:|
| Gross cooling | 37,800 | **43,500** | 49,000 |
| AHRI net total cooling | 36,800 | **42,000** | 47,500 |
| AHRI net sensible | 27,200 | **31,750** | 36,200 |
| Net latent | 9,600 | **10,250** | 11,300 |
| EER / SEER | 12.0 / 14 | **11.6 / 14** | 11.6 / 14 |
| Nominal / AHRI rated CFM | 1200 / 1200 | **1400 / 1400** | 1600 / 1600 |
| Heating @ 47 °F | 33,600 | **40,000** | 49,000 |
| Heating @ 17 °F | 19,400 | **24,200** | 29,800 |
| COP @ 47 °F | 3.48 | **3.6** | 3.8 |
| Net weight (lb) | 517 | **521** | 535 |
| Indoor blower | 1/2 HP direct, 3-spd | **1/2 HP direct, 3-spd** | 1/2 HP direct, 3-spd |

**Two items to verify before ordering:**
1. **The JK suffix in the 042 frame.** The catalog general-data table lists the 3.5-ton column as
   **A042CK**. Capacity data is identical across suffixes within a size (A036DL / DM / JK all show
   36,800), so the ratings above hold — but confirm `RJPL-A042JK000` is an orderable model, or take
   the correct suffix for 208/230V single phase.
2. **AHRI reference number and HSPF.** The PDF shows `AHRI ref: TBD` because I will not invent a
   certification number. HSPF is carried at 8.0 from the 036/048 (both rate 8.0); confirm for the 042.

## 11.2 Manual S — 3.5 ton

Loads unchanged: sensible 28,253 · latent 984 · **total 29,237 Btuh**

| | Capacity | % of load | Limit |
|---|---:|---:|---:|
| Sensible | 33,266 Btuh | **118%** | ≤115% |
| Latent | 1,099 Btuh | 112% | — |
| Total | 34,364 Btuh | **118%** | ≤115% |
| SHR | 97% | | |

**118% is 3 points over the 115% Manual S ceiling** — versus 133% for the 4 ton and 103% for the
3 ton. The Manual S page in the PDF now reads *"REVIEW: total cooling capacity = 118% of load.
ACCA Manual S limit is 115%"* rather than the original "Meets all requirements."

**Do not expect this to improve on closer analysis.** Both units run at exactly **400 cfm/ton**
(1200 ÷ 3 and 1400 ÷ 3.5), so coil face velocity and SHR behaviour are equivalent and the airflow
difference is already embedded in each unit's AHRI rating. Scaling the 3-ton's design-condition
performance by the AHRI net ratio (42,000 ÷ 36,800 = 1.141) is therefore clean: sensible 33,266,
total 34,364, SHR 96.8% — internally consistent. **118% is the expected number, not a conservative
placeholder.**

Whether a reviewer accepts it depends on which Manual S provision they apply; Manual S allows
additional latitude for heat pumps and in dry climates. Confirm against Rheem's expanded performance
table at 108 °F / 79.0 °F EDB / 62.1 °F EWB before submitting, but plan on ~118%.

### Heating
| | Value |
|---|---|
| Heat loss | 13,391 Btuh |
| Output capacity at 37 °F | 34,032 Btuh — **254% of load** |
| Supplemental heat required | 0 Btuh |
| Capacity balance point | **10 °F** (3 ton: 15 °F) |

Academic in Phoenix — winter design is 37 °F and the 3 ton already never calls supplemental heat.

## 11.3 Room airflow — 3.5 ton

Air flow factors: heating **0.095** cfm/Btuh · cooling **0.052** cfm/Btuh

| ROOM | Area ft² | Htg Btuh | Clg Btuh | Htg cfm | Clg cfm |
|---|---:|---:|---:|---:|---:|
| BEDROOM 1 | 117 | 1,955 | 2,654 | 185 | 139 |
| BEDROOM 2 | 117 | 1,343 | 2,247 | 127 | 118 |
| LAUNDRY | 34 | 803 | 1,216 | 76 | 64 |
| BATH | 52 | 501 | 370 | 48 | 19 |
| LIVING ROOM | 162 | 4,522 | 12,993 | 428 | 682 |
| KITCHEN | 214 | 2,705 | 7,198 | 256 | 378 |
| **Total** | **696** | **11,822** | **26,679** | **1,120** | **1,400** |

Living room is still the pressure point: 682 cfm into 162 ft² = **4.2 cfm/ft²**, 49% of system
airflow into 23% of the floor area. Three supply outlets minimum, selected for throw against the
west glass (review comment 12).

## 11.4 Manual D — 3.5 ton: DUCTS MUST BE UPSIZED

At 1,400 cfm the duct pressure losses rise by (1400/1200)² = **1.36×**:

| | @ 1,200 cfm (3 ton) | @ 1,400 cfm (3.5 ton) |
|---|---:|---:|
| Coil | 0.14 | 0.19 |
| Supply diffusers | 0.03 | 0.04 |
| Return grilles | 0.03 | 0.04 |
| Filter | 0.08 | 0.11 |
| **Total losses** | **0.28** | **0.38** |
| External static pressure | 0.50 | 0.50 |
| **Available static pressure** | **0.22** | **0.12** |
| Supply / return available | 0.158 / 0.062 | **0.086 / 0.034** |
| **Friction rate** | 0.097 — OK | **0.053 — NOT OK** |

**0.053 in/100 ft is below ACCA's practical minimum of about 0.06**, which is why the Friction Rate
page now prints NOT OK. The duct system was sized at 0.097 and cannot carry 1,400 cfm at 0.053
without growing. The PDF carries the resized system:

| Run | 3 ton | **3.5 ton** | Design cfm |
|---|---:|---:|---:|
| Return rb1 | 18.0" | **22.0"** | 1,400 |
| Trunk st1 | 12.0" | **14.0"** | 436 htg |
| Trunk st2 | 16.0" ← was 14.0" | **16.0"** | 682 clg |
| Trunk st3 | 8.0" | **10.0"** | 124 htg |
| BEDROOM 1 | 8.0" | **10.0"** | 185 htg |
| BEDROOM 2 | 8.0" | **10.0"** | 127 htg |
| BATH | 6.0" | 6.0" — holds | 48 htg |
| LAUNDRY | 6.0" | **7.0"** | 76 htg |
| KITCHEN ×2 | 8.0" | **10.0"** each | 189 clg each |
| LIVING ROOM ×2 | 10.0" | **12.0"** each | 341 clg each |

**Note the blower.** The A042 carries the *same* 1/2 HP direct-drive 3-speed indoor motor as the
3 ton, but must move 17% more air. The 0.50 in H2O external static in the report is an input
assumption carried from the original, not a verified capability at 1,400 cfm. **Confirm 0.50" ESP
against the A042 blower table** — if it makes less, the friction rate drops further and the ducts
grow again.

Duct lengths and fitting equivalent lengths were carried over unchanged (TEL 226 ft supply / 64 ft
return). A 26% larger plan very likely has longer runs — remeasure (review comment 7).

## 11.5 Recommendation

The 3.5 ton is a defensible call and far better than the 4 ton. Two honest caveats:

- **It is 3 points over the Manual S limit** on my scaled figures. Pull the manufacturer's expanded
  performance data — it may clear 115% once the 1,400 cfm airflow is accounted for. If it does not,
  a reviewer can reject it.
- **It obligates a duct rework** — nine runs grow, including the return. That is real cost. The 3 ton
  needs none and sits at 103% of load.

If the reason for stepping up is capacity margin on design days, the cheapest path is still the
§9 items — the 51%/38% duct losses above all — not more tonnage.
