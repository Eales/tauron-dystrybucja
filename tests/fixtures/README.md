# Recorded Tauron responses

Minimal field projections of anonymous public API responses captured on
2026-09-27 during the investigation in issue #5. Values in retained fields are
unchanged. Coordinates, PPE fields and unrelated service metadata are omitted.
These are API reports, not measurements of power availability.

| Fixture | cityGAID | streetGAID | houseNo | fromDate / toDate |
|---|---|---|---|---|
| rakowiecka30 | 119431 | 898134 | 30 | 2026-09-27T00:00:00 / 2026-10-27T23:59:59 |
| polna1 | 118992 | 743609 | 1 | same as above |
| polna99999 | 118992 | 743609 | 99999 | same as above (deliberately invalid number) |
| siewierz1 | 134 | 300736 | 1 | 2026-09-27T13:00:00Z / 2026-10-02T13:00:00Z |
| unsupported | 107507 | 896182 | 1 | same as above |

Endpoint: `/waapi/outages/address`, with `getLightingSupport=true` and
`getServicedSwitchingoff=true`; the last two also used `getCoordinates=true`.
Tests freeze time or use these records only for parsing; no live API is called.
Language and time-boundary mutations in tests are explicitly synthetic.
