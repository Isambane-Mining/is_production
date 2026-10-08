## Production

Isambane Mining Frappe App for Production Records

#### Mining Simulation

The Mining Simulation page (Geo Planning) runs the mine planner's simulator on
**Mining Simulation Project** documents. Import a `.rollover.json` project file from the
Mining Simulation Project list (*Import Simulation File*); *Export Simulation File* on a
project gives the same file back for the standalone simulator.

The page is built from the planner's standalone `simulator.html` without changing her code.
When she sends a new version, rebuild the page and test it:

```
bench --site <site> execute is_production.geo_planning.mining_simulation_sync.sync --kwargs "{'source': '/path/to/simulator.html'}"
```

#### License

mit