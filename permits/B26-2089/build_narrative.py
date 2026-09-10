#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/jayserver/jay-os/permits/B26-2089")
from mkpdf import Doc, PAGE_W, ML, MR

d = Doc()

d.text("SCOPE OF WORK NARRATIVE", size=16, bold=True, after=1)
d.text("Response to Deficiency Notice - Town of Queen Creek Building Safety", size=10.5, after=4)
d.rule(pad=5)

rows = [
    ("Permit Number:", "B26-2089"),
    ("Project Address:", "18882 E Vallejo St, #8, Queen Creek, AZ 85142"),
    ("Parcel (APN):", "314-14-919"),
    ("Date:", "September 9, 2026"),
    ("Prepared By:", "Professional CAD Design LLC - Hever V. Lopez, Company Owner"),
    ("", "623-249-1025 - jay@professionalcadesign.com"),
]
for label, val in rows:
    d.need(15)
    if label:
        d.ops.append("BT /F2 10.0 Tf %.1f %.1f Td (%s) Tj ET" % (ML, d.y - 10, label))
    d.ops.append("BT /F1 10.0 Tf %.1f %.1f Td (%s) Tj ET" % (ML + 108, d.y - 10, val.replace("(", r"\(").replace(")", r"\)")))
    d.y -= 14.5
d.rule(pad=6)

d.heading("1. Purpose of This Narrative", pre=6)
d.text("This narrative is submitted in response to the deficiency notice issued for Permit B26-2089, "
       "which requested a written description of the exact scope of work and clarification of what the "
       "electrical work is for and what it will serve. Section 2 states the scope in summary and "
       "Section 3 states the design basis.", after=2)

d.heading("2. Summary of Scope of Work")
d.text("Install one (1) new dedicated 200-amp, 120/240-volt, single-phase, underground-fed, utility-metered "
       "electrical service to serve the detached accessory structure on this parcel - specifically the "
       "detached casita and the attached golf simulator room.", after=6)
d.text("The new 200-amp service is separate from and in addition to the existing 400-amp service that "
       "serves the main residence. The scope of this permit includes the new service lateral, new meter "
       "and 200-amp main service panel at the accessory structure, the associated concrete demolition and "
       "replacement required to route the new underground conduit, the grounding electrode system for the "
       "new service, and the branch-circuit distribution within the casita and golf simulator room.", after=6)
d.text("No modification to the existing 400-amp service equipment, existing meter, or the main residence "
       "panel schedule is proposed under this permit.", bold=False, after=2)

d.heading("3. Design Basis")
d.text("The detached casita and golf simulator room are served by their own dedicated 200-amp service "
       "rather than as an extension of the existing panel at the main residence.", after=6)
d.text("Extending the existing panel to serve these two structures was evaluated and is not cost-efficient "
       "for this project. The existing panel also does not have the remaining capacity or breaker spaces "
       "to carry the added accessory-structure load. A dedicated service sized for the two accessory "
       "structures was selected instead.", after=6)
d.text("No panel upgrade is proposed under this permit. The existing 400-amp service equipment, existing "
       "meter, and the main residence panel schedule all remain as they are today.", after=6)
d.text("Providing a dedicated service places the service disconnect and distribution equipment at the "
       "structures being served, which shortens the feeder runs, leaves the existing circuits at the main "
       "residence undisturbed, separately meters the accessory-structure load, and simplifies future "
       "maintenance and load additions at the casita and simulator room.", after=6)
d.text("The plan sheets currently on file show the previously intended approach - a 200-amp sub panel "
       "and a 100-amp sub panel at the accessory structures, fed from the existing 400-amp service "
       "(site plan keynotes 1, 9 and 10; floor plan keynotes 1 and 12). Revised sheets showing the new "
       "dedicated 200-amp metered service described in this narrative will be provided.", after=2)

d.heading("4. Codes and Standards")
d.text("All work will be performed in accordance with the code editions currently adopted by the Town of "
       "Queen Creek, including the National Electrical Code and the 2021 International Residential Code "
       "with Town of Queen Creek amendments, and in accordance with the serving utility's service "
       "requirements. All materials and equipment will be listed and labeled for the application.", after=6)
d.text("Anticipated inspections: underground / trench prior to backfill, grounding electrode, rough "
       "electrical, concrete replacement as applicable, and final electrical.", after=2)

d.heading("5. Point of Contact")
d.text("Please direct any questions regarding this narrative or requests for additional information to:", after=6)
d.text("Hever V. Lopez, Company Owner", bold=True, after=1)
d.text("Professional CAD Design LLC", after=1)
d.text("623-249-1025  |  jay@professionalcadesign.com", after=2)

size, pages = d.finish("/home/jayserver/jay-os/permits/B26-2089/B26-2089_Scope_of_Work_Narrative.pdf",
                       footer="Permit B26-2089  |  18882 E Vallejo St #8, Queen Creek, AZ 85142  |  Scope of Work Narrative")
print("wrote %d bytes, %d pages" % (size, pages))
