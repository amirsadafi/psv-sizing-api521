# psv-sizing-api521
## API 521 PSV Sizing Tool

## Overview

This project aims to develop an industrial-grade Pressure Safety Valve (PSV) sizing application that follows:

* API Standard 521
* API Standard 520 Part I
* API Standard 520 Part II
* ASME Section VIII (where applicable)

The objective is to reproduce the engineering workflow used by experienced process engineers during pressure relief system design.

Unlike simple PSV calculators, this application evaluates credible overpressure scenarios, calculates relieving loads, determines governing cases, performs discharge backpressure analysis, sizes the PSV, and generates complete engineering calculation reports.

---

# Main Features

## Equipment Wizard

Supports:

* Pressure Vessels
* Columns
* Reactors
* Heat Exchangers
* Air Coolers
* Compressors
* Pumps
* Storage Tanks
* Pipelines
* Custom Equipment

---

## Credible Overpressure Scenario Assessment

Automatic evaluation of API 521 scenarios including:

* Blocked Outlet
* External Fire
* Tube Rupture
* Gas Blowby
* Utility Failure
* Thermal Expansion
* Cooling Failure
* Control Valve Failure
* Pump Dead Head
* Compressor Failure
* Chemical Reaction
* Runaway Reaction
* Operator Error

The software determines whether each scenario is credible before performing calculations.

---

## Relieving Load Calculation

Scenario-specific calculations based on API 521.

Outputs include:

* Relieving Rate
* Relieving Conditions
* Governing Case
* Engineering Assumptions

---

## Backpressure Analysis

Calculates:

* Superimposed Backpressure
* Built-up Backpressure
* Total Backpressure

Includes pressure loss calculations for discharge piping.

---

## PSV Selection

Supports:

* Conventional PSV
* Balanced Bellows PSV
* Pilot Operated PSV

Automatic recommendation based on API requirements.

---

## API 520 Sizing

Calculates:

* Required Relief Area
* Correction Factors
* Standard API Orifice Selection

---

## Engineering Report

Automatically generates a calculation report suitable for EPC engineering documentation.

---

# Planned Architecture

```
frontend/
    UI
    Wizard
    Reports

backend/
    API
    Calculation Engine

core/
    api520/
    api521/
    fluids/
    thermodynamics/
    hydraulics/
    backpressure/
    scenarios/
    validation/
    reports/

tests/

examples/

docs/
```

---

# Technology Stack

* Python
* FastAPI
* React
* TypeScript
* Viktor SDK
* Pydantic
* NumPy
* SciPy
* ReportLab
* Docker

---

# Long-Term Roadmap

* HYSYS Integration
* Aspen Plus Integration
* Flare Network Analysis
* DIERS Methodology
* Two-Phase Relief
* Fire Radiation Calculations
* Fluid Property Package
* API 2000 Module
* Flare Header Hydraulics
* PSV Datasheet Generator
* Automatic Engineering Reports

---

# Status

🚧 Early Development

---

# License

MIT License
