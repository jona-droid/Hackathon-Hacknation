# Power Line Drone Maintenance Knowledge Base

## 1. Flight Safety Margins & Operational Limits
- **Cable Standoff Distance**: Maintain a minimum distance of 2.5 meters when traveling along the span. During static inspection, do not close within 1.5 meters under any circumstances.
- **Maximum Approach Speed**: Cap velocity at 1.0 m/s when within 4.0 meters of high-voltage cables and 0.5 m/s within 2.0 meters.
- **Altitude Range**: Maintain clearance between 15.0m and 35.0m above ground. Do not descend below 20.0m when traversing the mid-span road crossing.
- **Wind Accommodation**: High-voltage corridors experience vortex shedding around pylons; reduce speed by 30% when crossing the windward side of tower arms.

## 2. Component Inspection Protocols
- **Insulator Strings (Pylon Top)**:
  - Approach horizontally from the lateral side at an elevation matching the insulator bracket.
  - Hover stable for at least 3 seconds at a standoff distance of 2.0m to 2.5m to capture clear high-resolution imagery.
  - Check for ceramic disk cracking, flashover burn marks, and cotter pin displacement.
- **Conductor Cables & Mid-Span Sag**:
  - The cable catenary dips by approximately 4 meters at mid-span between 60m and 120m spans.
  - Adjust flight altitude downwards progressively to follow cable sag while keeping a constant 2.5m standoff.
- **Vegetation Clearance (Tree Zones)**:
  - Maintain a 4.0m radial exclusion zone around any mature trees adjacent to the right-of-way.
  - Be alert for bird nests and branch intrusion near Span 1 (x = 30m).

## 3. Anomaly Response Procedures
- **Unplanned Drift**: If gusts exceed compensation authority, immediately gain 3.0m altitude to clear the top shield wire and hold position.
- **Sensor Glare**: If morning sun creates blinding reflections off conductors, yaw +30° to maintain oblique visual contrast against the ground.

## 4. Operator Insights Log
- **[Insulators i3/i4 – Visual Inspection]** (t≈91–105s): The pilot circled the insulators closely because one appeared a different color from the other. Treat a color difference between insulators on the same structure as a reason for a close visual check for possible defects. Note that the pilot also said "I don't know what I'm doing," so this heuristic has low confidence and should be checked with an experienced inspector.
- **[Insulator i5]**: When one insulator in a set looks a different colour from its neighbours (here the left unit at i5, seen while holding position about 7 m away at t≈133.7s), treat it as a possible defect and stop to inspect it more closely. The colour difference is a visual cue that the unit needs checking before it is cleared.
- **[Power Line Cables / Altitude Positioning]**: When maneuvering or circling near the line (t≈93.4s, ~10.5 m from the cable, ~5.75 m above it, near insulator i3), the pilot climbs to stay above the cables. The stated reason is to avoid interference, likely electromagnetic or compass interference ("inference" in the transcript).
- **[Cable Span near Pylon 1 / Tree Zone, t≈65–76s]**: When the pilot spotted damage on the conductor, they left the straight along-line track and flew a curved path off the line toward the tree side to inspect the damaged cable section. The drone held about 6 m horizontal and 1.4 m vertical from the cable, with the tree about 7 m away and 4.3 m/s wind. Suspected cable damage is a reason to deviate from the nominal route for a closer look, while still keeping clearance from the conductor and nearby vegetation.
- **[Insulator i1]**: When the first hover pass doesn't give a clear view of an insulator, circle back and inspect it from a different side. At t≈45s, viewing i1 from the other side (≈4.4 m away, holding position) showed that part of the insulator was broken, damage that was not apparent from the initial viewing angle.
- **[Insulator i1 / Below-level inspection approach]**: When a break was suspected in the middle of insulator i1, the pilot approached from slightly below the conductor level (about 0.7 m below the cable, ~5.3 m standoff) instead of level with it, to get a view of the possibly damaged middle section (t≈23.5s). The pilot's answer cut off mid-sentence, so the full rationale is unconfirmed.
- **[Insulator i3 / Adjacent Cable Section]**: When ice or frozen buildup is seen on the cable just below an insulator (e.g., i3), slow down and hold steady about 5 m from the cable to inspect that section closely (t=70.1s, 6.5 m/s wind). Icing on conductors near insulators is a visual defect cue that warrants dedicated inspection.
- **[Insulator i1 / Conductor Proximity]**: At t=21.9s the pilot held position about 4.8 m from insulator i1 rather than moving closer, because energized cables run right beside it (cable_dist ≈ 4.83 m). When inspecting an insulator next to live conductors, keep a standoff of roughly 4–5 m from the cables instead of closing in on the insulator.
- **[Insulator i3 / Inspection Dwell Time]**: At t≈50.4s, hovering about 5.6 m from insulator i3, the pilot saw nothing wrong on the quick visual check, so they left without a longer hover and changed direction to continue the route. Rationale: extended inspection is not needed when the initial pass shows the insulator is in good condition.
- **[Takeoff / Initial Approach – Insulator i1 area]**: After takeoff, the pilot climbed above working altitude before descending (observed around t≈8.2s) to get an overview of the target object and its surroundings. The stated reason was to see and analyze the object in context before descending to inspection height.
- **[Conductor / Mid-span Cable Segment, t≈61s]**: When the pilot suspects ice or a frozen deposit on a cable, they descend to just below the cable (≈1.7 m horizontal, ≈0.4 m below) and circle around it at low speed to inspect it up close. The reason is to visually check the suspected ice accumulation from multiple angles.
- **[Insulator i1 / Close Inspection Positioning]**: When inspecting insulator i1 (t≈23.1s, ~5.9 m standoff), the pilot preferred holding a stable hover offset in height from the cable rather than approaching level with the conductor, because it is easier to position and gives a view of the underside of the insulator.
- *[t=112.9s]* **Maneuver Insight**: I was going around the cable to see if it was fine on the other side too !
- *[t=70.0s]* **Maneuver Insight**: Because the road was there and IM not allowed to cross the road
- *[t=46.2s]* **Maneuver Insight**: I was checking the cable for analysis of the state of it. To make sure it wasn't broken!
- *[t=0.0s]* **Maneuver Insight**: I observed corrosion on the ceramic disk, so I stayed 2.2m away to avoid sparking.
- *[t=0.0s]* **Pylon 2 Hover Insight**: I noticed intense wind drafts between towers so I lowered speed to 0.4m/s to stabilize.
- *[Benchmark Flight]*: Initial baseline established for Pylons 1, 2, and 3 spans.

## 5. Expert Competence Grid
Knowledge: 3/29 slots filled, 0 confirmed in later flights.

### Pre-flight checks, take-off and overview
- **Route plan** (heard once): When planning the inspection, always check both sides of the line: start on one side, then fly over the high-voltage lines to inspect the other side.
- **Overview climb** (heard once): When you climb to overview height before going close, look along the cable for anything abnormal, missing parts or broken glass, then move to the other side and check that nothing is wrong there either.

### Approach to a pylon
- **Tower check** (heard once): When checking the tower, look for bird nests and pay particular attention to any nest built close to the electric line, because a nest near the conductor is a concern.
