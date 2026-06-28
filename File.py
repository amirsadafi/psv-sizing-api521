"""
PSV Sizing Application — API 521 / API 520 / ASME Section VIII
==============================================================
A professional multi-step VIKTOR application for Pressure Safety Valve sizing.
"""
import logging
import viktor as vkt
import plotly.graph_objects as go

from calculations.scenarios import screen_scenarios
from calculations.fire_case import (
    calculate_fire_heat_input_si,
    calculate_fire_vapor_rate,
    get_f_factor_options,
    get_f_factor_value,
)
from calculations.relieving_conditions import (
    calculate_relieving_pressure,
    calculate_relieving_temperature_gas,
    calculate_relieving_temperature_liquid,
)
from calculations.gas_sizing import size_gas_vapor, calculate_Kb
from calculations.liquid_sizing import size_liquid, calculate_Kv, calculate_Kw
from calculations.backpressure import calculate_backpressure
from utils.orifice_tables import select_orifice
from utils.validation import (
    validate_pressures,
    validate_fluid_properties,
    validate_orifice_sizing,
    validate_backpressure,
)
from report.report_generator import generate_report

logger = logging.getLogger("viktor")

PIPE_SIZES = ['1"', '1.5"', '2"', '3"', '4"', '6"', '8"', '10"', '12"']
PIPE_SCHEDULES = ["Sch 40", "Sch 80", "Sch 160", "XXS"]
F_FACTOR_OPTIONS = get_f_factor_options()


# ══════════════════════════════════════════════════════════════════════════════
# PARAMETRIZATION
# ══════════════════════════════════════════════════════════════════════════════
class Parametrization(vkt.Parametrization):

    # ── STEP 1: Equipment Information ─────────────────────────────────────────
    step1 = vkt.Step("1 · Equipment Info", views=["view_validation"])

    step1.sec_project = vkt.Section("Project Information")
    step1.sec_project.project_name = vkt.TextField("Project Name", default="My Project")
    step1.sec_project.doc_number = vkt.TextField("Document Number", default="PSV-CALC-001")
    step1.sec_project.revision = vkt.TextField("Revision", default="0")
    step1.sec_project.prepared_by = vkt.TextField("Prepared By", default="Engineer")
    step1.sec_project.tag_number = vkt.TextField("P&ID Tag Number", default="PSV-101")

    step1.sec_equip = vkt.Section("Equipment Information")
    step1.sec_equip.equipment_type = vkt.OptionField(
        "Equipment Type",
        options=["Pressure Vessel", "Separator", "Reactor", "Heat Exchanger",
                 "Air Cooler", "Storage Tank", "Column", "Pipeline",
                 "Pump", "Compressor", "Other"],
        default="Pressure Vessel",
    )
    step1.sec_equip.service = vkt.OptionField(
        "Service",
        options=["Gas", "Vapor", "Steam", "Liquid", "Two-Phase"],
        default="Gas",
    )
    step1.sec_equip.fluid_name = vkt.TextField("Fluid Name", default="Hydrocarbon Gas")
    step1.sec_equip.unit_system = vkt.OptionField(
        "Unit System", options=["SI", "US Customary"], default="SI"
    )
    step1.sec_equip.connected_equipment = vkt.TextField(
        "Connected Equipment (optional)", default=""
    )

    step1.sec_pressures = vkt.Section("Operating & Design Conditions")
    step1.sec_pressures.info_pressures = vkt.Text(
        "ℹ️ Enter all pressures in **gauge** (barg). "
        "The application converts to absolute internally."
    )
    step1.sec_pressures.op_pressure = vkt.NumberField(
        "Operating Pressure", suffix="barg", default=10.0, min=0.0,
        description="Normal operating pressure (gauge). Must be < set pressure."
    )
    step1.sec_pressures.op_temp = vkt.NumberField(
        "Operating Temperature", suffix="°C", default=100.0
    )
    step1.sec_pressures.design_pressure = vkt.NumberField(
        "Design Pressure", suffix="barg", default=15.0, min=0.0
    )
    step1.sec_pressures.design_temp = vkt.NumberField(
        "Design Temperature", suffix="°C", default=150.0
    )
    step1.sec_pressures.mawp = vkt.NumberField(
        "MAWP", suffix="barg", default=14.0, min=0.0,
        description="Maximum Allowable Working Pressure as stamped on nameplate."
    )
    step1.sec_pressures.set_pressure = vkt.NumberField(
        "PSV Set Pressure", suffix="barg", default=13.0, min=0.0,
        description="PSV set pressure (gauge). Must be <= MAWP per ASME UG-134."
    )
    step1.sec_pressures.volume = vkt.NumberField(
        "Vessel Volume (optional)", suffix="m³", default=10.0, min=0.0
    )
    step1.sec_pressures.elevation = vkt.NumberField(
        "Elevation (optional)", suffix="m", default=0.0
    )

    step1.sec_fluid = vkt.Section("Fluid Properties at Relieving Conditions")
    step1.sec_fluid.info_fluid = vkt.Text(
        "ℹ️ Enter fluid properties at **relieving conditions** "
        "(relieving pressure and temperature)."
    )
    step1.sec_fluid.mw = vkt.NumberField(
        "Molecular Weight (MW)", suffix="kg/kmol", default=28.0, min=1.0
    )
    step1.sec_fluid.k_ratio = vkt.NumberField(
        "Specific Heat Ratio (k = Cp/Cv)", default=1.3, min=1.0, max=1.8,
        description="Typical: air=1.4, steam=1.3, hydrocarbons=1.1-1.3."
    )
    step1.sec_fluid.z_factor = vkt.NumberField(
        "Compressibility Factor (Z)", default=1.0, min=0.1, max=1.5,
        description="Z=1.0 for ideal gas."
    )
    step1.sec_fluid.viscosity = vkt.NumberField(
        "Dynamic Viscosity", suffix="cP", default=0.01, min=0.0
    )
    step1.sec_fluid.density = vkt.NumberField(
        "Density at Relieving Conditions", suffix="kg/m³", default=50.0, min=0.0
    )
    step1.sec_fluid.latent_heat = vkt.NumberField(
        "Latent Heat of Vaporization", suffix="kJ/kg", default=300.0, min=0.0,
        description="Required for fire case and condensing failure scenarios."
    )
    step1.sec_fluid.specific_gravity = vkt.NumberField(
        "Liquid Specific Gravity (G)", default=0.7, min=0.0,
        description="Relative to water at 60°F. Required for liquid sizing."
    )

    # ── STEP 2: Scenario Screening ────────────────────────────────────────────
    step2 = vkt.Step("2 · Scenario Screening", views=["view_scenario_screening"])

    step2.sec_intro = vkt.Section("Screening Instructions")
    step2.sec_intro.info_screening = vkt.Text(
        "**API 521 Scenario Screening**\n\n"
        "Answer Yes/No for each equipment configuration question. "
        "The application determines which overpressure scenarios are credible."
    )

    step2.sec_questions = vkt.Section("Equipment Configuration Questions")
    step2.sec_questions.q_blocked_outlet = vkt.BooleanField(
        "Can the equipment outlet be blocked? (valve closure, plugging, operator error)",
        default=True
    )
    step2.sec_questions.q_fire_exposure = vkt.BooleanField(
        "Is the equipment within a fire zone and does it contain flammable fluid?",
        default=True
    )
    step2.sec_questions.q_thermal_expansion = vkt.BooleanField(
        "Can liquid be thermally trapped in a liquid-full section?", default=False
    )
    step2.sec_questions.q_tube_rupture = vkt.BooleanField(
        "Is there a heat exchanger with a high-pressure tube side?", default=False
    )
    step2.sec_questions.q_cooling_water = vkt.BooleanField(
        "Does the equipment rely on cooling water for safe operation?", default=False
    )
    step2.sec_questions.q_condenser = vkt.BooleanField(
        "Is there a condenser in the system?", default=False
    )
    step2.sec_questions.q_reflux = vkt.BooleanField(
        "Is there a reflux system (distillation column)?", default=False
    )
    step2.sec_questions.q_cv_fail_open = vkt.BooleanField(
        "Is there an inlet control valve that can fail open?", default=False
    )
    step2.sec_questions.q_cv_fail_closed = vkt.BooleanField(
        "Is there an outlet control valve that can fail closed?", default=False
    )
    step2.sec_questions.q_gas_blowby = vkt.BooleanField(
        "Can high-pressure gas blow by into this lower-pressure equipment?", default=False
    )
    step2.sec_questions.q_pump = vkt.BooleanField(
        "Is there a pump connected to this equipment?", default=False
    )
    step2.sec_questions.q_compressor = vkt.BooleanField(
        "Is there a compressor connected to this equipment?", default=False
    )
    step2.sec_questions.q_reaction = vkt.BooleanField(
        "Is there a chemical or exothermic reaction occurring?", default=False
    )
    step2.sec_questions.q_steam_coil = vkt.BooleanField(
        "Is there a steam coil or steam injection in the equipment?", default=False
    )
    step2.sec_questions.q_power_failure = vkt.BooleanField(
        "Can power failure affect the system?", default=False
    )
    step2.sec_questions.q_instrument_air = vkt.BooleanField(
        "Is instrument air required for safe operation of control valves?", default=False
    )
    step2.sec_questions.q_check_valve = vkt.BooleanField(
        "Is there a check valve that could fail and expose equipment to higher pressure?",
        default=False
    )
    step2.sec_questions.q_heat_input = vkt.BooleanField(
        "Is there a fired heater or electric heater providing heat input?", default=False
    )
    step2.sec_questions.q_operator_error = vkt.BooleanField(
        "Should operator error be considered as a credible scenario?", default=False
    )

    # ── STEP 3: Relieving Load Calculations ───────────────────────────────────
    step3 = vkt.Step("3 · Relieving Loads", views=["view_relieving_loads"])

    step3.sec_intro3 = vkt.Section("Relieving Load Inputs")
    step3.sec_intro3.info3 = vkt.Text(
        "**Relieving Load Calculation**\n\n"
        "Enter inputs for each credible scenario. "
        "The application uses the appropriate API 521 equation for each case."
    )

    step3.sec_blocked = vkt.Section("Blocked Outlet")
    step3.sec_blocked.max_inlet_flow = vkt.NumberField(
        "Maximum Inlet Flow Rate", suffix="kg/h", default=5000.0, min=0.0,
        description="Maximum flow from upstream source at relieving conditions."
    )

    step3.sec_fire = vkt.Section("External Fire Case")
    step3.sec_fire.wetted_area = vkt.NumberField(
        "Wetted Surface Area (A_w)", suffix="m²", default=50.0, min=0.0
    )
    step3.sec_fire.f_factor = vkt.OptionField(
        "Environment Factor (F)", options=F_FACTOR_OPTIONS, default=F_FACTOR_OPTIONS[0],
        description="F-factor from API 521 Table 5. Use 1.0 for bare vessel."
    )
    step3.sec_fire.drainage_adequate = vkt.BooleanField(
        "Is drainage and firefighting adequate?", default=True,
        description="Adequate: C1=21,000. Inadequate: C1=34,500 (API 521 §5.15.1.2)."
    )

    step3.sec_cooling = vkt.Section("Cooling Failure / Condenser Failure")
    step3.sec_cooling.condenser_duty = vkt.NumberField(
        "Condenser / Cooler Heat Duty (Q)", suffix="kW", default=1000.0, min=0.0
    )

    step3.sec_reaction = vkt.Section("Runaway Reaction")
    step3.sec_reaction.reaction_heat = vkt.NumberField(
        "Maximum Reaction Heat Generation Rate", suffix="kW", default=500.0, min=0.0
    )

    step3.sec_other = vkt.Section("Other Scenarios")
    step3.sec_other.other_flow = vkt.NumberField(
        "Relieving Load for Other Scenarios", suffix="kg/h", default=0.0, min=0.0
    )

    # ── STEP 4: Governing Scenario & Relieving Conditions ─────────────────────
    step4 = vkt.Step("4 · Governing Scenario", views=["view_governing"])

    step4.sec_gov = vkt.Section("Governing Scenario Selection")
    step4.sec_gov.info4 = vkt.Text(
        "**Governing Scenario**\n\n"
        "The application identifies the scenario with the highest relieving load. "
        "Confirm whether the governing case is the fire case (21% overpressure) "
        "or a non-fire case (10% overpressure) per ASME UG-125."
    )
    step4.sec_gov.is_fire_case = vkt.BooleanField(
        "Is the governing scenario the External Fire case?", default=False,
        description="Fire case: 21% overpressure (ASME UG-125(c)(3)). "
                    "Non-fire: 10% overpressure."
    )
    step4.sec_gov.has_rupture_disk = vkt.BooleanField(
        "Is a rupture disk installed upstream of the PSV?", default=False,
        description="If yes, Kc = 0.9 is applied per API 520."
    )

    # ── STEP 5: Backpressure Calculation ──────────────────────────────────────
    step5 = vkt.Step("5 · Backpressure", views=["view_backpressure"])

    step5.sec_dest = vkt.Section("Discharge Destination")
    step5.sec_dest.discharge_dest = vkt.OptionField(
        "Discharge Destination",
        options=["Atmosphere", "Closed Flare Header", "Open Flare Stack",
                 "Blowdown Drum", "Other"],
        default="Closed Flare Header",
    )
    step5.sec_dest.superimposed_bp = vkt.NumberField(
        "Superimposed Backpressure", suffix="barg", default=0.5, min=0.0,
        description="Known constant backpressure at PSV outlet (header pressure)."
    )

    step5.sec_pipe = vkt.Section("Tailpipe Configuration")
    step5.sec_pipe.pipe_length = vkt.NumberField(
        "Discharge Pipe Length", suffix="m", default=20.0, min=0.0
    )
    step5.sec_pipe.pipe_size = vkt.OptionField(
        "Nominal Pipe Size", options=PIPE_SIZES, default='4"'
    )
    step5.sec_pipe.pipe_schedule = vkt.OptionField(
        "Pipe Schedule", options=PIPE_SCHEDULES, default="Sch 40"
    )
    step5.sec_pipe.pipe_roughness = vkt.NumberField(
        "Pipe Roughness (ε)", suffix="mm", default=0.046, min=0.0,
        description="Default 0.046 mm for carbon steel."
    )
    step5.sec_pipe.elevation_change = vkt.NumberField(
        "Elevation Change (Δz)", suffix="m", default=0.0,
        description="Positive = upward flow (increases backpressure)."
    )

    step5.sec_fittings = vkt.Section("Pipe Fittings")
    step5.sec_fittings.n_elbows_90 = vkt.IntegerField(
        "Number of 90° Elbows", default=2, min=0
    )
    step5.sec_fittings.n_elbows_45 = vkt.IntegerField(
        "Number of 45° Elbows", default=0, min=0
    )
    step5.sec_fittings.n_tees_through = vkt.IntegerField(
        "Number of Tees (flow-through)", default=0, min=0
    )
    step5.sec_fittings.n_tees_branch = vkt.IntegerField(
        "Number of Tees (branch flow)", default=0, min=0
    )
    step5.sec_fittings.n_gate_valves = vkt.IntegerField(
        "Number of Gate Valves", default=1, min=0
    )
    step5.sec_fittings.n_globe_valves = vkt.IntegerField(
        "Number of Globe Valves", default=0, min=0
    )

    # ── STEP 6: PSV Type Selection & API 520 Sizing ───────────────────────────
    step6 = vkt.Step("6 · PSV Type & Sizing", views=["view_sizing"])

    step6.sec_type = vkt.Section("PSV Type Selection")
    step6.sec_type.info6 = vkt.Text(
        "**PSV Type Recommendation**\n\n"
        "The application recommends a PSV type based on backpressure and service. "
        "You may override the recommendation below."
    )
    step6.sec_type.psv_type_override = vkt.OptionField(
        "PSV Type (override or accept recommendation)",
        options=["Auto (use recommendation)", "Conventional",
                 "Balanced Bellows", "Pilot Operated"],
        default="Auto (use recommendation)",
    )

    step6.sec_sizing = vkt.Section("Sizing Parameters")
    step6.sec_sizing.info_sizing = vkt.Text(
        "**API 520 Sizing**\n\n"
        "The sizing equation is automatically selected based on service type. "
        "Correction factors are calculated per API 520 Part I."
    )
    step6.sec_sizing.kc_rupture_disk = vkt.BooleanField(
        "Apply Kc = 0.9 (rupture disk upstream of PSV)?", default=False,
        description="Per API 520, Kc = 0.9 when a rupture disk is installed upstream."
    )

    # ── STEP 7: Engineering Report ────────────────────────────────────────────
    step7 = vkt.Step("7 · Report", views=["view_report"])

    step7.sec_report = vkt.Section("Report Generation")
    step7.sec_report.info_report = vkt.Text(
        "**Engineering Calculation Report**\n\n"
        "The report is generated automatically from all inputs and calculations. "
        "It includes scenario screening, relieving load calculations, backpressure "
        "analysis, PSV type selection, API 520 sizing, and selected orifice.\n\n"
        "Suitable for inclusion in an EPC engineering package."
    )
    step7.sec_report.download_report = vkt.DownloadButton(
        "⬇ Download HTML Report", method="download_report"
    )


# ══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def _recommend_psv_type(total_bp_pct: float, built_up_bp_pct: float, service: str) -> tuple[str, str]:
    """
    Recommend PSV type based on backpressure and service per API 520 Part I §4.

    Returns:
        (psv_type_str, justification_str)
    """
    if service == "Liquid":
        # Liquid service: conventional is usually acceptable unless high BP
        if total_bp_pct <= 10.0:
            return (
                "Conventional",
                "Liquid service with total backpressure ≤ 10% of set pressure. "
                "Conventional PSV is acceptable per API 520 Part I §4.2."
            )
        elif total_bp_pct <= 30.0:
            return (
                "Balanced Bellows",
                f"Liquid service with total backpressure = {total_bp_pct:.1f}% of set pressure. "
                "Balanced bellows PSV recommended to maintain stable operation per API 520 §4.3."
            )
        else:
            return (
                "Pilot Operated",
                f"Liquid service with total backpressure = {total_bp_pct:.1f}% of set pressure. "
                "Pilot-operated PSV required for high backpressure service per API 520 §4.4."
            )

    # Gas / Vapor / Steam / Two-Phase
    if built_up_bp_pct <= 10.0 and total_bp_pct <= 10.0:
        return (
            "Conventional",
            f"Built-up backpressure = {built_up_bp_pct:.1f}% of set pressure (≤ 10% limit). "
            "Conventional PSV is acceptable per API 520 Part I §4.2. "
            "No backpressure correction to set pressure required."
        )
    elif total_bp_pct <= 30.0:
        return (
            "Balanced Bellows",
            f"Built-up backpressure = {built_up_bp_pct:.1f}% of set pressure (> 10% limit). "
            f"Total backpressure = {total_bp_pct:.1f}% (≤ 30% limit for balanced bellows). "
            "Balanced bellows PSV recommended per API 520 §4.3. "
            "Bellows isolates spring from backpressure, maintaining set pressure accuracy."
        )
    else:
        return (
            "Pilot Operated",
            f"Total backpressure = {total_bp_pct:.1f}% of set pressure (> 30% limit for balanced bellows). "
            "Pilot-operated PSV required per API 520 §4.4. "
            "POPRV maintains full lift at high backpressure and is unaffected by backpressure "
            "up to 50% (or higher with manufacturer confirmation)."
        )


def _collect_scenario_loads(params) -> list[dict]:
    """
    Collect all scenario relieving loads into a comparable list.
    Returns list of dicts: {name, load_kg_h, basis, is_fire}.
    """
    loads = []
    p = params

    # ── Blocked Outlet ────────────────────────────────────────────────────────
    if p.step2.sec_questions.q_blocked_outlet:
        loads.append({
            "name": "Blocked Outlet",
            "load_kg_h": p.step3.sec_blocked.max_inlet_flow or 0.0,
            "basis": "Maximum inlet flow rate at relieving conditions (API 521 §5.15).",
            "is_fire": False,
            "equation": "W = Maximum inlet flow rate (direct input)",
        })

    # ── External Fire ─────────────────────────────────────────────────────────
    if p.step2.sec_questions.q_fire_exposure:
        try:
            f_val = get_f_factor_value(p.step3.sec_fire.f_factor)
            fire_result = calculate_fire_heat_input_si(
                wetted_area_m2=p.step3.sec_fire.wetted_area or 0.0,
                f_factor=f_val,
                drainage_adequate=p.step3.sec_fire.drainage_adequate,
            )
            latent = p.step1.sec_fluid.latent_heat or 300.0
            vapor_result = calculate_fire_vapor_rate(
                Q_kw=fire_result["Q_kw"],
                latent_heat_kj_per_kg=latent,
            )
            loads.append({
                "name": "External Fire",
                "load_kg_h": vapor_result["vapor_rate_kg_h"],
                "basis": (
                    f"API 521 §5.15.1.2 fire case. "
                    f"Q = {fire_result['Q_kw']:.1f} kW, λ = {latent:.1f} kJ/kg."
                ),
                "is_fire": True,
                "equation": fire_result["equation"],
                "Q_kw": fire_result["Q_kw"],
            })
        except Exception as e:
            logger.warning(f"⚠️ Fire case calculation error: {e}")

    # ── Cooling / Condenser Failure ───────────────────────────────────────────
    if p.step2.sec_questions.q_cooling_water or p.step2.sec_questions.q_condenser:
        latent = p.step1.sec_fluid.latent_heat or 300.0
        duty_kw = p.step3.sec_cooling.condenser_duty or 0.0
        if latent > 0 and duty_kw > 0:
            load_kg_h = (duty_kw / latent) * 3600.0
            loads.append({
                "name": "Cooling / Condenser Failure",
                "load_kg_h": load_kg_h,
                "basis": (
                    f"Loss of cooling duty Q = {duty_kw:.1f} kW. "
                    f"Vapor generated = Q / λ = {duty_kw:.1f} / {latent:.1f} × 3600 "
                    f"= {load_kg_h:.1f} kg/h (API 521 §5.16)."
                ),
                "is_fire": False,
                "equation": "W = Q / λ × 3600  [API 521 §5.16]",
            })

    # ── Runaway Reaction ──────────────────────────────────────────────────────
    if p.step2.sec_questions.q_reaction:
        latent = p.step1.sec_fluid.latent_heat or 300.0
        rxn_kw = p.step3.sec_reaction.reaction_heat or 0.0
        if latent > 0 and rxn_kw > 0:
            load_kg_h = (rxn_kw / latent) * 3600.0
            loads.append({
                "name": "Runaway Reaction",
                "load_kg_h": load_kg_h,
                "basis": (
                    f"Maximum reaction heat generation = {rxn_kw:.1f} kW. "
                    f"Vapor generated = Q / λ = {rxn_kw:.1f} / {latent:.1f} × 3600 "
                    f"= {load_kg_h:.1f} kg/h (API 521 §5.20)."
                ),
                "is_fire": False,
                "equation": "W = Q_rxn / λ × 3600  [API 521 §5.20]",
            })

    # ── Other Scenarios ───────────────────────────────────────────────────────
    other_load = p.step3.sec_other.other_flow or 0.0
    if other_load > 0:
        loads.append({
            "name": "Other / User-Defined",
            "load_kg_h": other_load,
            "basis": "User-defined relieving load for additional scenarios.",
            "is_fire": False,
            "equation": "W = User input (kg/h)",
        })

    return loads


def _determine_governing(loads: list[dict]) -> dict | None:
    """Return the scenario with the highest relieving load."""
    if not loads:
        return None
    return max(loads, key=lambda x: x["load_kg_h"])


def _run_full_calculation(params) -> dict:
    """
    Execute the complete PSV sizing calculation chain.
    Returns a comprehensive results dict used by all views and the report.
    """
    p = params
    results = {}

    # ── 1. Pressure validation ────────────────────────────────────────────────
    press_warnings = validate_pressures(
        op_pressure_barg=p.step1.sec_pressures.op_pressure or 0.0,
        set_pressure_barg=p.step1.sec_pressures.set_pressure or 0.0,
        mawp_barg=p.step1.sec_pressures.mawp or 0.0,
        design_pressure_barg=p.step1.sec_pressures.design_pressure or 0.0,
    )
    fluid_warnings = validate_fluid_properties(
        k=p.step1.sec_fluid.k_ratio,
        z=p.step1.sec_fluid.z_factor,
        mw=p.step1.sec_fluid.mw,
        viscosity_cp=p.step1.sec_fluid.viscosity,
    )
    all_warnings = press_warnings + fluid_warnings
    results["validation_messages"] = all_warnings

    # ── 2. Scenario screening ─────────────────────────────────────────────────
    answers = {
        "q_blocked_outlet":   p.step2.sec_questions.q_blocked_outlet,
        "q_fire_exposure":    p.step2.sec_questions.q_fire_exposure,
        "q_thermal_expansion":p.step2.sec_questions.q_thermal_expansion,
        "q_tube_rupture":     p.step2.sec_questions.q_tube_rupture,
        "q_cooling_water":    p.step2.sec_questions.q_cooling_water,
        "q_condenser":        p.step2.sec_questions.q_condenser,
        "q_reflux":           p.step2.sec_questions.q_reflux,
        "q_cv_fail_open":     p.step2.sec_questions.q_cv_fail_open,
        "q_cv_fail_closed":   p.step2.sec_questions.q_cv_fail_closed,
        "q_gas_blowby":       p.step2.sec_questions.q_gas_blowby,
        "q_pump":             p.step2.sec_questions.q_pump,
        "q_compressor":       p.step2.sec_questions.q_compressor,
        "q_reaction":         p.step2.sec_questions.q_reaction,
        "q_steam_coil":       p.step2.sec_questions.q_steam_coil,
        "q_power_failure":    p.step2.sec_questions.q_power_failure,
        "q_instrument_air":   p.step2.sec_questions.q_instrument_air,
        "q_check_valve":      p.step2.sec_questions.q_check_valve,
        "q_heat_input":       p.step2.sec_questions.q_heat_input,
        "q_operator_error":   p.step2.sec_questions.q_operator_error,
    }
    scenario_results = screen_scenarios(answers)
    results["scenario_results"] = scenario_results
    logger.info(f"✅ Scenario screening: {sum(1 for s in scenario_results if s.credible == 'Yes')} credible scenarios")

    # ── 3. Relieving loads ────────────────────────────────────────────────────
    loads = _collect_scenario_loads(p)
    results["loads"] = loads
    governing = _determine_governing(loads)
    results["governing"] = governing
    logger.info(f"✅ Governing scenario: {governing['name'] if governing else 'None'}, "
                f"load = {governing['load_kg_h']:.1f} kg/h" if governing else "No governing scenario")

    # ── 4. Relieving conditions ───────────────────────────────────────────────
    set_p = p.step1.sec_pressures.set_pressure or 0.0
    is_fire = p.step4.sec_gov.is_fire_case
    has_rd = p.step4.sec_gov.has_rupture_disk
    rel_press = calculate_relieving_pressure(set_p, is_fire, has_rd)

    service = p.step1.sec_equip.service
    op_p_barg = p.step1.sec_pressures.op_pressure or 0.0
    op_p_bara = op_p_barg + 1.01325
    op_temp_c = p.step1.sec_pressures.op_temp or 20.0

    if service == "Liquid":
        rel_temp = calculate_relieving_temperature_liquid(op_temp_c)
    else:
        rel_temp = calculate_relieving_temperature_gas(
            op_temp_c=op_temp_c,
            op_pressure_bara=op_p_bara,
            relieving_pressure_bara=rel_press["relieving_pressure_bara"],
        )

    results["relieving_pressure"] = rel_press
    results["relieving_temperature"] = rel_temp

    # ── 5. Backpressure ───────────────────────────────────────────────────────
    relieving_load_kg_h = governing["load_kg_h"] if governing else 1.0
    density = p.step1.sec_fluid.density or 50.0
    viscosity = p.step1.sec_fluid.viscosity or 0.01

    try:
        bp_result = calculate_backpressure(
            flow_rate_kg_h=relieving_load_kg_h,
            density_kg_m3=density,
            viscosity_cp=viscosity,
            pipe_length_m=p.step5.sec_pipe.pipe_length or 20.0,
            nominal_size=p.step5.sec_pipe.pipe_size or '4"',
            schedule=p.step5.sec_pipe.pipe_schedule or "Sch 40",
            roughness_mm=p.step5.sec_pipe.pipe_roughness or 0.046,
            elevation_change_m=p.step5.sec_pipe.elevation_change or 0.0,
            n_elbows_90=p.step5.sec_fittings.n_elbows_90 or 0,
            n_elbows_45=p.step5.sec_fittings.n_elbows_45 or 0,
            n_tees_through=p.step5.sec_fittings.n_tees_through or 0,
            n_tees_branch=p.step5.sec_fittings.n_tees_branch or 0,
            n_gate_valves=p.step5.sec_fittings.n_gate_valves or 0,
            n_globe_valves=p.step5.sec_fittings.n_globe_valves or 0,
            superimposed_bp_barg=p.step5.sec_dest.superimposed_bp or 0.0,
        )
        bp_result["nominal_size"] = p.step5.sec_pipe.pipe_size
        bp_result["schedule"] = p.step5.sec_pipe.pipe_schedule
        logger.info(f"✅ Backpressure: built-up = {bp_result['built_up_bp_bar']:.4f} bar, "
                    f"total = {bp_result['total_bp_bar']:.4f} bar")
    except Exception as e:
        logger.warning(f"⚠️ Backpressure calculation error: {e}")
        bp_result = {
            "built_up_bp_bar": 0.0, "total_bp_bar": p.step5.sec_dest.superimposed_bp or 0.0,
            "superimposed_bp_barg": p.step5.sec_dest.superimposed_bp or 0.0,
            "dP_friction_bar": 0.0, "dP_elevation_bar": 0.0,
            "velocity_m_s": 0.0, "reynolds_number": 0.0, "friction_factor": 0.0,
            "L_straight_m": 0.0, "L_fittings_eq_m": 0.0, "L_total_m": 0.0,
            "pipe_id_mm": 0.0, "relative_roughness": 0.0,
            "nominal_size": p.step5.sec_pipe.pipe_size, "schedule": p.step5.sec_pipe.pipe_schedule,
            "error": str(e),
        }
    results["backpressure"] = bp_result

    # ── 6. Backpressure percentages ───────────────────────────────────────────
    set_p_bara = set_p + 1.01325
    built_up_pct = (bp_result["built_up_bp_bar"] / set_p_bara * 100.0) if set_p_bara > 0 else 0.0
    total_bp_pct = (bp_result["total_bp_bar"] / set_p_bara * 100.0) if set_p_bara > 0 else 0.0
    results["built_up_bp_pct"] = built_up_pct
    results["total_bp_pct"] = total_bp_pct

    # ── 7. PSV type recommendation ────────────────────────────────────────────
    override = p.step6.sec_type.psv_type_override
    rec_type, rec_justification = _recommend_psv_type(total_bp_pct, built_up_pct, service)
    if override and override != "Auto (use recommendation)":
        psv_type = override
        psv_justification = (
            f"User override: {override}. "
            f"(Automatic recommendation was: {rec_type}. {rec_justification})"
        )
    else:
        psv_type = rec_type
        psv_justification = rec_justification
    results["psv_type"] = psv_type
    results["psv_justification"] = psv_justification
    logger.info(f"✅ PSV type: {psv_type}")

    # ── 8. Backpressure validation ────────────────────────────────────────────
    bp_warnings = validate_backpressure(built_up_pct, total_bp_pct, psv_type)
    all_warnings += bp_warnings
    results["validation_messages"] = all_warnings

    # ── 9. Correction factors ─────────────────────────────────────────────────
    Kd_gas = 0.975
    Kd_liq = 0.65
    Kc = 0.9 if (p.step4.sec_gov.has_rupture_disk or p.step6.sec_sizing.kc_rupture_disk) else 1.0
    Kb = calculate_Kb(total_bp_pct, psv_type)
    results["Kd_gas"] = Kd_gas
    results["Kd_liq"] = Kd_liq
    results["Kc"] = Kc
    results["Kb"] = Kb

    # ── 10. API 520 Sizing ────────────────────────────────────────────────────
    P1_bara = rel_press["relieving_pressure_bara"]
    P2_bara = bp_result["total_bp_bar"] + 1.01325  # total BP as absolute
    T_k = rel_temp["relieving_temp_k"]
    mw = p.step1.sec_fluid.mw or 28.0
    k = p.step1.sec_fluid.k_ratio or 1.3
    z = p.step1.sec_fluid.z_factor or 1.0
    sg = p.step1.sec_fluid.specific_gravity or 0.7
    visc = p.step1.sec_fluid.viscosity or 0.01

    sizing_result = None
    try:
        if service == "Liquid":
            # Estimate Reynolds number for Kv (use a preliminary area estimate)
            Q_m3_h = relieving_load_kg_h / (sg * 1000.0)  # rough conversion
            # Preliminary Kv = 1.0 for first pass
            Kw = calculate_Kw(built_up_pct, psv_type)
            sizing_result = size_liquid(
                Q_m3_h=Q_m3_h,
                specific_gravity=sg,
                P1_bara=P1_bara,
                P2_bara=P2_bara,
                Kd=Kd_liq,
                Kw=Kw,
                Kc=Kc,
                Kv=1.0,
            )
            sizing_result["service"] = "Liquid"
            sizing_result["Kw"] = Kw
        else:
            sizing_result = size_gas_vapor(
                W_kg_h=relieving_load_kg_h,
                k=k,
                mw=mw,
                T_k=T_k,
                P1_bara=P1_bara,
                z=z,
                Kd=Kd_gas,
                Kb=Kb,
                Kc=Kc,
            )
            sizing_result["service"] = service
        logger.info(f"✅ Required area: {sizing_result['required_area_in2']:.4f} in² "
                    f"({sizing_result['required_area_mm2']:.1f} mm²)")
    except Exception as e:
        logger.warning(f"⚠️ Sizing calculation error: {e}")
        sizing_result = {"error": str(e), "required_area_in2": 0.0, "required_area_mm2": 0.0}

    results["sizing"] = sizing_result

    # ── 11. Orifice selection ─────────────────────────────────────────────────
    req_area = sizing_result.get("required_area_in2", 0.0) if sizing_result else 0.0
    orifice = select_orifice(req_area)
    results["orifice"] = orifice

    # ── 12. Orifice validation ────────────────────────────────────────────────
    if orifice and sizing_result:
        or_warnings = validate_orifice_sizing(
            required_area_in2=req_area,
            selected_area_in2=orifice.get("area_in2"),
            designation=orifice.get("designation", ""),
        )
        all_warnings += or_warnings
        results["validation_messages"] = all_warnings

    logger.info(f"✅ Selected orifice: {orifice.get('designation', 'N/A')} "
                f"({orifice.get('area_in2', 'N/A')} in²)")

    return results


# ══════════════════════════════════════════════════════════════════════════════
# CONTROLLER
# ══════════════════════════════════════════════════════════════════════════════
class Controller(vkt.Controller):
    """
    PSV Sizing Application Controller.
    Implements all view methods and the download button handler.
    Each view calls _run_full_calculation() to ensure consistent results.
    """
    parametrization = Parametrization

    # ── Step 1 View: Input Validation ─────────────────────────────────────────
    @vkt.WebView("✅ Input Validation", duration_guess=2)
    def view_validation(self, params, **kwargs) -> vkt.WebResult:
        """Display input validation results and pressure hierarchy check."""
        p = params
        press_msgs = validate_pressures(
            op_pressure_barg=p.step1.sec_pressures.op_pressure or 0.0,
            set_pressure_barg=p.step1.sec_pressures.set_pressure or 0.0,
            mawp_barg=p.step1.sec_pressures.mawp or 0.0,
            design_pressure_barg=p.step1.sec_pressures.design_pressure or 0.0,
        )
        fluid_msgs = validate_fluid_properties(
            k=p.step1.sec_fluid.k_ratio,
            z=p.step1.sec_fluid.z_factor,
            mw=p.step1.sec_fluid.mw,
            viscosity_cp=p.step1.sec_fluid.viscosity,
        )
        all_msgs = press_msgs + fluid_msgs

        # Build pressure hierarchy diagram
        op_p = p.step1.sec_pressures.op_pressure or 0.0
        set_p = p.step1.sec_pressures.set_pressure or 0.0
        mawp = p.step1.sec_pressures.mawp or 0.0
        dp = p.step1.sec_pressures.design_pressure or 0.0
        is_fire = p.step4.sec_gov.is_fire_case
        overpressure_pct = 21.0 if is_fire else 10.0
        set_p_bara = set_p + 1.01325
        rel_p_barg = set_p_bara * (1.0 + overpressure_pct / 100.0) - 1.01325

        # Validation message HTML
        msg_html = ""
        if not all_msgs:
            msg_html = '<div class="msg info">✅ All inputs are valid. No issues detected.</div>'
        for m in all_msgs:
            css = {"ERROR": "msg error", "WARNING": "msg warning", "INFO": "msg info"}.get(m.level, "msg info")
            icon = {"ERROR": "❌", "WARNING": "⚠️", "INFO": "ℹ️"}.get(m.level, "ℹ️")
            msg_html += f'<div class="{css}">{icon} <strong>[{m.level}]</strong> {m.message}</div>'

        # Pressure hierarchy table
        rows = [
            ("Operating Pressure",  f"{op_p:.2f} barg",  "Normal operating condition"),
            ("PSV Set Pressure",    f"{set_p:.2f} barg",  "PSV opens at this pressure (≤ MAWP per ASME UG-134)"),
            ("MAWP",                f"{mawp:.2f} barg",   "Maximum Allowable Working Pressure (nameplate)"),
            ("Design Pressure",     f"{dp:.2f} barg",     "Vessel design pressure (typically ≥ MAWP)"),
            ("Relieving Pressure",  f"{rel_p_barg:.2f} barg",
             f"Set P × (1 + {overpressure_pct:.0f}%) — {'Fire case' if is_fire else 'Non-fire case'} (ASME UG-125)"),
        ]
        table_rows = "".join(
            f"<tr><td>{r[0]}</td><td><strong>{r[1]}</strong></td><td>{r[2]}</td></tr>"
            for r in rows
        )

        # Fluid properties summary
        fluid_rows = [
            ("Fluid Name",          p.step1.sec_equip.fluid_name or "—"),
            ("Service",             p.step1.sec_equip.service or "—"),
            ("Equipment Type",      p.step1.sec_equip.equipment_type or "—"),
            ("Molecular Weight",    f"{p.step1.sec_fluid.mw or '—'} kg/kmol"),
            ("Specific Heat Ratio k", f"{p.step1.sec_fluid.k_ratio or '—'}"),
            ("Compressibility Z",   f"{p.step1.sec_fluid.z_factor or '—'}"),
            ("Viscosity",           f"{p.step1.sec_fluid.viscosity or '—'} cP"),
            ("Density",             f"{p.step1.sec_fluid.density or '—'} kg/m³"),
            ("Latent Heat",         f"{p.step1.sec_fluid.latent_heat or '—'} kJ/kg"),
        ]
        fluid_table_rows = "".join(
            f"<tr><td>{r[0]}</td><td>{r[1]}</td></tr>" for r in fluid_rows
        )

        html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
        <title>Input Validation</title>
        <style>
            body {{ font-family: Arial, sans-serif; font-size: 11pt; margin: 24px; color: #1a1a1a; }}
            h2 {{ color: #003366; border-bottom: 2px solid #003366; padding-bottom: 6px; }}
            h3 {{ color: #336699; margin-top: 20px; }}
            table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
            th {{ background: #003366; color: white; padding: 8px 12px; text-align: left; }}
            td {{ padding: 6px 12px; border: 1px solid #ccc; }}
            tr:nth-child(even) {{ background: #f5f8fc; }}
            .msg {{ padding: 8px 14px; margin: 6px 0; border-radius: 4px; font-size: 10pt; }}
            .error {{ background: #f8d7da; border-left: 4px solid #dc3545; }}
            .warning {{ background: #fff3cd; border-left: 4px solid #ffc107; }}
            .info {{ background: #d1ecf1; border-left: 4px solid #17a2b8; }}
            .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px;
                      font-size: 9pt; font-weight: bold; }}
            .badge-ok {{ background: #d4edda; color: #155724; }}
            .badge-warn {{ background: #fff3cd; color: #856404; }}
            .badge-err {{ background: #f8d7da; color: #721c24; }}
        </style></head><body>
        <h2>📋 Step 1 — Equipment &amp; Input Validation</h2>
        <h3>Validation Messages</h3>
        {msg_html}
        <h3>Pressure Hierarchy (ASME UG-125 / API 521)</h3>
        <table>
            <thead><tr><th>Parameter</th><th>Value</th><th>Basis</th></tr></thead>
            <tbody>{table_rows}</tbody>
        </table>
        <h3>Fluid Properties Summary</h3>
        <table>
            <thead><tr><th>Property</th><th>Value</th></tr></thead>
            <tbody>{fluid_table_rows}</tbody>
        </table>
        <p style="font-size:9pt;color:#888;margin-top:30px;">
            ℹ️ All pressures in barg (gauge). Absolute = gauge + 1.01325 bar.
            Relieving pressure calculated per ASME UG-125.
        </p>
        </body></html>"""
        return vkt.WebResult(html=html)

    # ── Step 2 View: Scenario Screening ──────────────────────────────────────
    @vkt.WebView("🔍 Scenario Screening", duration_guess=2)
    def view_scenario_screening(self, params, **kwargs) -> vkt.WebResult:
        """Display API 521 scenario screening results table."""
        p = params
        answers = {
            "q_blocked_outlet":    p.step2.sec_questions.q_blocked_outlet,
            "q_fire_exposure":     p.step2.sec_questions.q_fire_exposure,
            "q_thermal_expansion": p.step2.sec_questions.q_thermal_expansion,
            "q_tube_rupture":      p.step2.sec_questions.q_tube_rupture,
            "q_cooling_water":     p.step2.sec_questions.q_cooling_water,
            "q_condenser":         p.step2.sec_questions.q_condenser,
            "q_reflux":            p.step2.sec_questions.q_reflux,
            "q_cv_fail_open":      p.step2.sec_questions.q_cv_fail_open,
            "q_cv_fail_closed":    p.step2.sec_questions.q_cv_fail_closed,
            "q_gas_blowby":        p.step2.sec_questions.q_gas_blowby,
            "q_pump":              p.step2.sec_questions.q_pump,
            "q_compressor":        p.step2.sec_questions.q_compressor,
            "q_reaction":          p.step2.sec_questions.q_reaction,
            "q_steam_coil":        p.step2.sec_questions.q_steam_coil,
            "q_power_failure":     p.step2.sec_questions.q_power_failure,
            "q_instrument_air":    p.step2.sec_questions.q_instrument_air,
            "q_check_valve":       p.step2.sec_questions.q_check_valve,
            "q_heat_input":        p.step2.sec_questions.q_heat_input,
            "q_operator_error":    p.step2.sec_questions.q_operator_error,
        }
        scenario_results = screen_scenarios(answers)
        credible_count = sum(1 for s in scenario_results if s.credible == "Yes")
        review_count = sum(1 for s in scenario_results if s.credible == "Review")

        rows_html = ""
        for s in scenario_results:
            if s.credible == "Yes":
                badge = '<span style="color:#155724;font-weight:bold;background:#d4edda;padding:2px 8px;border-radius:10px;">✅ YES</span>'
            elif s.credible == "Review":
                badge = '<span style="color:#856404;font-weight:bold;background:#fff3cd;padding:2px 8px;border-radius:10px;">⚠️ REVIEW</span>'
            else:
                badge = '<span style="color:#6c757d;background:#f8f9fa;padding:2px 8px;border-radius:10px;">❌ No</span>'
            rows_html += (
                f"<tr><td><strong>{s.name}</strong></td>"
                f"<td style='text-align:center'>{badge}</td>"
                f"<td>{s.basis}</td>"
                f"<td style='font-size:9pt;color:#555'>{s.api_ref}</td></tr>"
            )

        html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
        <title>Scenario Screening</title>
        <style>
            body {{ font-family: Arial, sans-serif; font-size: 11pt; margin: 24px; color: #1a1a1a; }}
            h2 {{ color: #003366; border-bottom: 2px solid #003366; padding-bottom: 6px; }}
            h3 {{ color: #336699; margin-top: 20px; }}
            table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
            th {{ background: #003366; color: white; padding: 8px 12px; text-align: left; }}
            td {{ padding: 7px 12px; border: 1px solid #ccc; vertical-align: top; }}
            tr:nth-child(even) {{ background: #f5f8fc; }}
            .summary {{ background: #eef2f7; border-left: 4px solid #003366;
                        padding: 10px 16px; margin-bottom: 16px; border-radius: 4px; }}
        </style></head><body>
        <h2>🔍 Step 2 — API 521 Scenario Screening</h2>
        <div class="summary">
            <strong>Screening Summary:</strong>
            {credible_count} credible scenario(s) identified &nbsp;|&nbsp;
            {review_count} scenario(s) require further review &nbsp;|&nbsp;
            {len(scenario_results) - credible_count - review_count} scenario(s) screened out.
            <br/><small>Proceed to Step 3 to calculate relieving loads for credible scenarios.</small>
        </div>
        <table>
            <thead>
                <tr>
                    <th style="width:22%">Overpressure Scenario</th>
                    <th style="width:12%;text-align:center">Credible?</th>
                    <th style="width:50%">Basis for Determination</th>
                    <th style="width:16%">API 521 Reference</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
        <p style="font-size:9pt;color:#888;margin-top:20px;">
            Per API 521, 6th Edition. Scenario credibility based on equipment configuration
            answers provided in Step 2. All credible scenarios must be evaluated for relieving load.
        </p>
        </body></html>"""
        return vkt.WebResult(html=html)

    # ── Step 3 View: Relieving Loads ──────────────────────────────────────────
    @vkt.WebView("⚖️ Relieving Loads", duration_guess=3)
    def view_relieving_loads(self, params, **kwargs) -> vkt.WebResult:
        """Calculate and display relieving loads for all credible scenarios."""
        loads = _collect_scenario_loads(params)
        governing = _determine_governing(loads)

        if not loads:
            html = """<!DOCTYPE html><html><body style="font-family:Arial;margin:24px;">
            <h2 style="color:#003366;">⚖️ Step 3 — Relieving Load Calculations</h2>
            <div style="background:#fff3cd;border-left:4px solid #ffc107;padding:10px 16px;">
            ⚠️ No credible scenarios identified. Return to Step 2 and answer the
            screening questions to identify applicable overpressure scenarios.
            </div></body></html>"""
            return vkt.WebResult(html=html)

        rows_html = ""
        for load in loads:
            is_gov = governing and load["name"] == governing["name"]
            row_style = 'style="background:#d4edda;font-weight:bold;"' if is_gov else ""
            gov_badge = " 🏆 <em>GOVERNING</em>" if is_gov else ""
            rows_html += (
                f"<tr {row_style}>"
                f"<td><strong>{load['name']}</strong>{gov_badge}</td>"
                f"<td style='text-align:right'><strong>{load['load_kg_h']:.1f}</strong></td>"
                f"<td style='text-align:right'>{load['load_kg_h'] * 2.20462:.1f}</td>"
                f"<td>{'🔥 Fire Case (21% OP)' if load['is_fire'] else 'Non-fire (10% OP)'}</td>"
                f"<td style='font-size:9pt'>{load['equation']}</td>"
                f"</tr>"
            )

        gov_html = ""
        if governing:
            gov_html = f"""
            <div style="background:#d4edda;border-left:4px solid #28a745;
                        padding:12px 16px;margin:16px 0;border-radius:4px;">
                <strong>🏆 Governing Scenario: {governing['name']}</strong><br/>
                Relieving Load = <strong>{governing['load_kg_h']:.1f} kg/h
                ({governing['load_kg_h'] * 2.20462:.1f} lb/h)</strong><br/>
                <small>{governing['basis']}</small>
            </div>"""

        html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
        <title>Relieving Loads</title>
        <style>
            body {{ font-family: Arial, sans-serif; font-size: 11pt; margin: 24px; color: #1a1a1a; }}
            h2 {{ color: #003366; border-bottom: 2px solid #003366; padding-bottom: 6px; }}
            h3 {{ color: #336699; margin-top: 20px; }}
            table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
            th {{ background: #003366; color: white; padding: 8px 12px; text-align: left; }}
            td {{ padding: 7px 12px; border: 1px solid #ccc; vertical-align: top; }}
            tr:nth-child(even) {{ background: #f5f8fc; }}
        </style></head><body>
        <h2>⚖️ Step 3 — Relieving Load Calculations</h2>
        {gov_html}
        <h3>All Credible Scenario Loads</h3>
        <table>
            <thead>
                <tr>
                    <th>Scenario</th>
                    <th style="text-align:right">Load (kg/h)</th>
                    <th style="text-align:right">Load (lb/h)</th>
                    <th>Case Type</th>
                    <th>Equation / Method</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
        <p style="font-size:9pt;color:#888;margin-top:20px;">
            The governing scenario is the one with the highest relieving load.
            Fire case uses 21% overpressure; all other cases use 10% (ASME UG-125).
            Proceed to Step 4 to confirm governing scenario and relieving conditions.
        </p>
        </body></html>"""
        return vkt.WebResult(html=html)

    # ── Step 4 View: Governing Scenario & Relieving Conditions ───────────────
    @vkt.WebView("🌡️ Governing Scenario", duration_guess=3)
    def view_governing(self, params, **kwargs) -> vkt.WebResult:
        """Display governing scenario determination and relieving conditions."""
        p = params
        loads = _collect_scenario_loads(p)
        governing = _determine_governing(loads)

        set_p = p.step1.sec_pressures.set_pressure or 0.0
        is_fire = p.step4.sec_gov.is_fire_case
        has_rd = p.step4.sec_gov.has_rupture_disk
        rel_press = calculate_relieving_pressure(set_p, is_fire, has_rd)

        service = p.step1.sec_equip.service
        op_p_barg = p.step1.sec_pressures.op_pressure or 0.0
        op_p_bara = op_p_barg + 1.01325
        op_temp_c = p.step1.sec_pressures.op_temp or 20.0

        if service == "Liquid":
            rel_temp = calculate_relieving_temperature_liquid(op_temp_c)
        else:
            rel_temp = calculate_relieving_temperature_gas(
                op_temp_c=op_temp_c,
                op_pressure_bara=op_p_bara,
                relieving_pressure_bara=rel_press["relieving_pressure_bara"],
            )

        gov_name = governing["name"] if governing else "No scenario identified"
        gov_load = f"{governing['load_kg_h']:.1f} kg/h ({governing['load_kg_h'] * 2.20462:.1f} lb/h)" if governing else "—"

        cond_rows = [
            ("Governing Scenario",       gov_name),
            ("Governing Relieving Load", gov_load),
            ("Case Type",                "🔥 Fire Case (21% overpressure)" if is_fire else "Non-fire Case (10% overpressure)"),
            ("Rupture Disk Upstream",    "Yes — Kc = 0.9 applied" if has_rd else "No — Kc = 1.0"),
            ("", ""),
            ("Set Pressure",             f"{rel_press['set_pressure_barg']:.3f} barg  ({rel_press['set_pressure_bara']:.3f} bara)"),
            ("Overpressure Allowance",   f"{rel_press['overpressure_pct']:.0f}%  (ASME UG-125)"),
            ("Accumulation",             f"{rel_press['accumulation_bar']:.3f} bar"),
            ("Relieving Pressure",       f"{rel_press['relieving_pressure_barg']:.3f} barg  ({rel_press['relieving_pressure_bara']:.3f} bara)  ({rel_press['relieving_pressure_psia']:.2f} psia)"),
            ("", ""),
            ("Operating Temperature",    f"{op_temp_c:.1f} °C  ({op_temp_c * 9/5 + 32:.1f} °F)"),
            ("Relieving Temperature",    f"{rel_temp['relieving_temp_c']:.1f} °C  ({rel_temp['relieving_temp_k']:.2f} K)  ({rel_temp['relieving_temp_r']:.2f} °R)"),
            ("Temperature Method",       rel_temp.get("equation", rel_temp.get("note", "—"))),
            ("", ""),
            ("Molecular Weight",         f"{p.step1.sec_fluid.mw or '—'} kg/kmol"),
            ("Specific Heat Ratio k",    f"{p.step1.sec_fluid.k_ratio or '—'}"),
            ("Compressibility Z",        f"{p.step1.sec_fluid.z_factor or '—'}"),
            ("Density at Relieving P",   f"{p.step1.sec_fluid.density or '—'} kg/m³"),
            ("Viscosity",                f"{p.step1.sec_fluid.viscosity or '—'} cP"),
            ("Latent Heat",              f"{p.step1.sec_fluid.latent_heat or '—'} kJ/kg"),
        ]
        cond_rows_html = "".join(
            f"<tr><td style='font-weight:bold;background:#eef2f7;width:40%'>{r[0]}</td><td>{r[1]}</td></tr>"
            for r in cond_rows
        )

        html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
        <title>Governing Scenario</title>
        <style>
            body {{ font-family: Arial, sans-serif; font-size: 11pt; margin: 24px; color: #1a1a1a; }}
            h2 {{ color: #003366; border-bottom: 2px solid #003366; padding-bottom: 6px; }}
            h3 {{ color: #336699; margin-top: 20px; }}
            table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
            th {{ background: #003366; color: white; padding: 8px 12px; text-align: left; }}
            td {{ padding: 7px 12px; border: 1px solid #ccc; }}
            tr:nth-child(even) {{ background: #f5f8fc; }}
        </style></head><body>
        <h2>🌡️ Step 4 — Governing Scenario &amp; Relieving Conditions</h2>
        <h3>Relieving Conditions Summary</h3>
        <table><tbody>{cond_rows_html}</tbody></table>
        <p style="font-size:9pt;color:#888;margin-top:20px;">
            Relieving pressure per ASME UG-125. Gas relieving temperature estimated
            using ideal gas law: T_r = T_op × (P_r / P_op) per API 521 §5.4.3.
            For real gases, verify with equation of state.
        </p>
        </body></html>"""
        return vkt.WebResult(html=html)

    # ── Step 5 View: Backpressure Analysis ───────────────────────────────────
    @vkt.WebView("🔧 Backpressure Analysis", duration_guess=3)
    def view_backpressure(self, params, **kwargs) -> vkt.WebResult:
        """Calculate and display discharge system backpressure."""
        p = params
        loads = _collect_scenario_loads(p)
        governing = _determine_governing(loads)
        relieving_load_kg_h = governing["load_kg_h"] if governing else 1.0
        density = p.step1.sec_fluid.density or 50.0
        viscosity = p.step1.sec_fluid.viscosity or 0.01
        set_p = p.step1.sec_pressures.set_pressure or 1.0
        set_p_bara = set_p + 1.01325

        error_html = ""
        try:
            bp = calculate_backpressure(
                flow_rate_kg_h=relieving_load_kg_h,
                density_kg_m3=density,
                viscosity_cp=viscosity,
                pipe_length_m=p.step5.sec_pipe.pipe_length or 20.0,
                nominal_size=p.step5.sec_pipe.pipe_size or '4"',
                schedule=p.step5.sec_pipe.pipe_schedule or "Sch 40",
                roughness_mm=p.step5.sec_pipe.pipe_roughness or 0.046,
                elevation_change_m=p.step5.sec_pipe.elevation_change or 0.0,
                n_elbows_90=p.step5.sec_fittings.n_elbows_90 or 0,
                n_elbows_45=p.step5.sec_fittings.n_elbows_45 or 0,
                n_tees_through=p.step5.sec_fittings.n_tees_through or 0,
                n_tees_branch=p.step5.sec_fittings.n_tees_branch or 0,
                n_gate_valves=p.step5.sec_fittings.n_gate_valves or 0,
                n_globe_valves=p.step5.sec_fittings.n_globe_valves or 0,
                superimposed_bp_barg=p.step5.sec_dest.superimposed_bp or 0.0,
            )
            built_up_pct = bp["built_up_bp_bar"] / set_p_bara * 100.0
            total_bp_pct = bp["total_bp_bar"] / set_p_bara * 100.0

            # Determine API limits
            rec_type, _ = _recommend_psv_type(total_bp_pct, built_up_pct, p.step1.sec_equip.service)
            limits = {
                "Conventional": ("10%", built_up_pct > 10.0),
                "Balanced Bellows": ("30%", total_bp_pct > 30.0),
                "Pilot Operated": ("50%", total_bp_pct > 50.0),
            }
            limit_val, limit_exceeded = limits.get(rec_type, ("N/A", False))
            limit_color = "#dc3545" if limit_exceeded else "#28a745"
            limit_icon = "❌ EXCEEDS LIMIT" if limit_exceeded else "✅ Within limit"

            bp_rows = [
                ("Discharge Destination",    p.step5.sec_dest.discharge_dest or "—"),
                ("Pipe Size / Schedule",     f"{p.step5.sec_pipe.pipe_size} / {p.step5.sec_pipe.pipe_schedule}"),
                ("Pipe Internal Diameter",   f"{bp['pipe_id_mm']:.1f} mm"),
                ("Straight Pipe Length",     f"{bp['L_straight_m']:.1f} m"),
                ("Fittings Equiv. Length",   f"{bp['L_fittings_eq_m']:.2f} m"),
                ("Total Equiv. Length",      f"{bp['L_total_m']:.2f} m"),
                ("Flow Velocity",            f"{bp['velocity_m_s']:.3f} m/s"),
                ("Reynolds Number",          f"{bp['reynolds_number']:.0f}"),
                ("Relative Roughness (ε/D)", f"{bp['relative_roughness']:.6f}"),
                ("Darcy Friction Factor",    f"{bp['friction_factor']:.5f}"),
                ("Friction Pressure Drop",   f"{bp['dP_friction_bar']:.4f} bar"),
                ("Elevation Head",           f"{bp['dP_elevation_bar']:.4f} bar"),
                ("Built-up Backpressure",    f"{bp['built_up_bp_bar']:.4f} bar  ({built_up_pct:.2f}% of set P)"),
                ("Superimposed Backpressure",f"{bp['superimposed_bp_barg']:.4f} barg"),
                ("Total Backpressure",       f"{bp['total_bp_bar']:.4f} bar  ({total_bp_pct:.2f}% of set P)"),
                ("Calculation Method",       "Darcy-Weisbach + Colebrook-White (iterative)"),
                ("Fittings Method",          "Equivalent length (Crane TP-410 L/D ratios)"),
            ]
            bp_rows_html = "".join(
                f"<tr><td style='font-weight:bold;background:#eef2f7;width:40%'>{r[0]}</td><td>{r[1]}</td></tr>"
                for r in bp_rows
            )

            api_limits_html = f"""
            <h3>API Backpressure Limits</h3>
            <table>
                <thead><tr><th>PSV Type</th><th>Built-up BP Limit</th><th>Total BP Limit</th><th>Status</th></tr></thead>
                <tbody>
                    <tr>
                        <td>Conventional</td><td>10% of set P</td><td>10% of set P</td>
                        <td style="color:{'#dc3545' if built_up_pct > 10 else '#28a745'}">
                            {'❌ Exceeds' if built_up_pct > 10 else '✅ OK'} ({built_up_pct:.1f}%)
                        </td>
                    </tr>
                    <tr>
                        <td>Balanced Bellows</td><td>—</td><td>30% of set P</td>
                        <td style="color:{'#dc3545' if total_bp_pct > 30 else '#28a745'}">
                            {'❌ Exceeds' if total_bp_pct > 30 else '✅ OK'} ({total_bp_pct:.1f}%)
                        </td>
                    </tr>
                    <tr>
                        <td>Pilot Operated</td><td>—</td><td>50% of set P (typical)</td>
                        <td style="color:{'#dc3545' if total_bp_pct > 50 else '#28a745'}">
                            {'❌ Exceeds' if total_bp_pct > 50 else '✅ OK'} ({total_bp_pct:.1f}%)
                        </td>
                    </tr>
                </tbody>
            </table>
            <div style="background:#eef2f7;border-left:4px solid #003366;padding:10px 16px;margin:12px 0;">
                <strong>Recommended PSV Type based on backpressure: {rec_type}</strong>
                &nbsp;—&nbsp; API limit for {rec_type}: {limit_val}
                &nbsp;<span style="color:{limit_color}">{limit_icon}</span>
            </div>"""

        except Exception as e:
            bp_rows_html = f"<tr><td colspan='2' style='color:red'>Calculation error: {e}</td></tr>"
            api_limits_html = ""
            error_html = f'<div style="background:#f8d7da;border-left:4px solid #dc3545;padding:10px 16px;">❌ Error: {e}</div>'

        html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
        <title>Backpressure Analysis</title>
        <style>
            body {{ font-family: Arial, sans-serif; font-size: 11pt; margin: 24px; color: #1a1a1a; }}
            h2 {{ color: #003366; border-bottom: 2px solid #003366; padding-bottom: 6px; }}
            h3 {{ color: #336699; margin-top: 20px; }}
            table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
            th {{ background: #003366; color: white; padding: 8px 12px; text-align: left; }}
            td {{ padding: 7px 12px; border: 1px solid #ccc; }}
            tr:nth-child(even) {{ background: #f5f8fc; }}
        </style></head><body>
        <h2>🔧 Step 5 — Backpressure Analysis</h2>
        {error_html}
        <h3>Discharge System Pressure Drop Calculation</h3>
        <table><tbody>{bp_rows_html}</tbody></table>
        {api_limits_html}
        <p style="font-size:9pt;color:#888;margin-top:20px;">
            Backpressure calculated at governing relieving conditions.
            Darcy-Weisbach equation with Colebrook-White friction factor (iterative solution).
            Fittings modelled as equivalent pipe length per Crane TP-410.
        </p>
        </body></html>"""
        return vkt.WebResult(html=html)

    # ── Step 6 View: PSV Type & API 520 Sizing ────────────────────────────────
    @vkt.WebView("📐 PSV Sizing", duration_guess=4)
    def view_sizing(self, params, **kwargs) -> vkt.WebResult:
        """Display PSV type recommendation and API 520 sizing results."""
        try:
            results = _run_full_calculation(params)
        except Exception as e:
            logger.warning(f"⚠️ Sizing view error: {e}")
            html = f"""<!DOCTYPE html><html><body style="font-family:Arial;margin:24px;">
            <h2 style="color:#003366;">📐 Step 6 — PSV Type &amp; API 520 Sizing</h2>
            <div style="background:#f8d7da;border-left:4px solid #dc3545;padding:10px 16px;">
            ❌ Calculation error: {e}<br/>
            Please check all inputs in Steps 1–5 and try again.
            </div></body></html>"""
            return vkt.WebResult(html=html)

        psv_type = results["psv_type"]
        psv_just = results["psv_justification"]
        sizing = results.get("sizing", {})
        orifice = results.get("orifice", {})
        Kb = results.get("Kb", 1.0)
        Kc = results.get("Kc", 1.0)
        built_up_pct = results.get("built_up_bp_pct", 0.0)
        total_bp_pct = results.get("total_bp_pct", 0.0)
        service = params.step1.sec_equip.service

        # PSV type badge
        type_colors = {
            "Conventional": "#003366",
            "Balanced Bellows": "#1a6b3c",
            "Pilot Operated": "#7b2d00",
        }
        type_color = type_colors.get(psv_type, "#003366")

        # Sizing results table
        if sizing and "error" not in sizing:
            req_area_in2 = sizing.get("required_area_in2", 0.0)
            req_area_mm2 = sizing.get("required_area_mm2", 0.0)
            if service == "Liquid":
                sizing_rows = [
                    ("Service",                  "Liquid"),
                    ("Sizing Equation",          sizing.get("equation", "—")),
                    ("Relieving Flow Rate",       f"{sizing.get('Q_m3_h', 0):.3f} m\u00b3/h  ({sizing.get('Q_gpm', 0):.2f} US gpm)"),
                    ("Kd (Discharge Coeff.)",    f"{sizing.get('Kd', '—')}  (liquid service, API 520 §5)"),
                    ("Kw (Backpressure Corr.)",  f"{sizing.get('Kw', 1.0):.3f}"),
                    ("Kc (Combination Corr.)",   f"{Kc}  ({'rupture disk upstream' if Kc < 1.0 else 'no rupture disk'})"),
                    ("Kv (Viscosity Corr.)",     f"{sizing.get('Kv', 1.0):.3f}"),
                    ("Specific Gravity G",       f"{sizing.get('specific_gravity', '—')}"),
                    ("Relieving Pressure P1",    f"{sizing.get('P1_psia', 0):.2f} psia"),
                    ("Backpressure P2",          f"{sizing.get('P2_psia', 0):.2f} psia"),
                    ("dP (P1 - P2)",             f"{sizing.get('delta_P_psi', 0):.2f} psi"),
                    ("Required Orifice Area",    f"<strong>{req_area_in2:.4f} in2  ({req_area_mm2:.1f} mm2)</strong>"),
                    ("API Reference",            sizing.get("api_ref", "—")),
                ]
            else:
                sizing_rows = [
                    ("Service",                  service),
                    ("Sizing Equation",          sizing.get("equation", "—")),
                    ("Relieving Flow Rate",       f"{sizing.get('W_kg_h', 0):.1f} kg/h  ({sizing.get('W_lb_h', 0):.1f} lb/h)"),
                    ("C Coefficient",            f"{sizing.get('C', 0):.3f}  (from k = {sizing.get('k', '—')})"),
                    ("Kd (Discharge Coeff.)",    f"{sizing.get('Kd', '—')}  (gas/vapor, preliminary)"),
                    ("Kb (Backpressure Corr.)",  f"{Kb:.3f}  (backpressure = {total_bp_pct:.1f}% of set P)"),
                    ("Kc (Combination Corr.)",   f"{Kc}  ({'rupture disk upstream' if Kc < 1.0 else 'no rupture disk'})"),
                    ("Molecular Weight M",       f"{sizing.get('MW', '—')} kg/kmol"),
                    ("Compressibility Z",        f"{sizing.get('Z', '—')}"),
                    ("Relieving Temperature T",  f"{sizing.get('T_k', 0) - 273.15:.1f} C  ({sizing.get('T_R', 0):.2f} R)"),
                    ("Relieving Pressure P1",    f"{sizing.get('P1_bara', 0):.3f} bara  ({sizing.get('P1_psia', 0):.2f} psia)"),
                    ("Required Orifice Area",    f"<strong>{req_area_in2:.4f} in2  ({req_area_mm2:.1f} mm2)</strong>"),
                    ("API Reference",            sizing.get("api_ref", "—")),
                ]

            sizing_rows_html = "".join(
                f"<tr><td style='font-weight:bold;background:#eef2f7;width:40%'>{r[0]}</td><td>{r[1]}</td></tr>"
                for r in sizing_rows
            )

            # Orifice selection
            if orifice.get("designation") == "MULTIPLE":
                orifice_html = """
                <div style="background:#f8d7da;border-left:4px solid #dc3545;padding:10px 16px;margin:12px 0;">
                    MULTIPLE PSVs REQUIRED. Required area exceeds largest standard API orifice (T = 26.0 in2).
                    Multiple PSVs in parallel are required. Consult a senior process engineer.
                </div>"""
            else:
                des = orifice.get("designation", "—")
                area_in2 = orifice.get("area_in2", 0)
                area_mm2 = orifice.get("area_mm2", 0)
                ratio = orifice.get("oversizing_ratio", 1.0)
                ratio_color = "#dc3545" if ratio and ratio > 2.0 else "#155724"
                chatter_warn = "  WARNING: Ratio > 2.0 — chattering risk" if ratio and ratio > 2.0 else ""
                orifice_html = f"""
                <div style="background:#d4edda;border-left:4px solid #28a745;padding:12px 16px;margin:12px 0;border-radius:4px;">
                    <strong>Selected Standard API Orifice: Designation {des}</strong><br/>
                    Area = <strong>{area_in2} in2  ({area_mm2} mm2)</strong><br/>
                    Required = {req_area_in2:.4f} in2  |
                    Oversizing ratio = <span style="color:{ratio_color}"><strong>{ratio:.2f}x</strong></span>
                    {chatter_warn}
                </div>
                <h3>Standard API Orifice Table (API 520 Part I)</h3>
                <table>
                    <thead><tr><th>Designation</th><th>Area (in2)</th><th>Area (mm2)</th><th>Status</th></tr></thead>
                    <tbody>"""
                from utils.orifice_tables import get_orifice_table
                for row in get_orifice_table():
                    is_sel = row["designation"] == des
                    is_req = row["area_in2"] >= req_area_in2
                    row_style = 'style="background:#d4edda;font-weight:bold;"' if is_sel else ""
                    status = "SELECTED" if is_sel else ("Larger" if is_req else "Too small")
                    orifice_html += (
                        f"<tr {row_style}>"
                        f"<td>{row['designation']}</td>"
                        f"<td>{row['area_in2']}</td>"
                        f"<td>{row['area_mm2']}</td>"
                        f"<td>{status}</td></tr>"
                    )
                orifice_html += "</tbody></table>"

            sizing_content = f"""
            <h3>API 520 Sizing Calculation</h3>
            <table><tbody>{sizing_rows_html}</tbody></table>
            {orifice_html}"""
        else:
            err = sizing.get("error", "Unknown error") if sizing else "No sizing result"
            sizing_content = f'<div style="background:#f8d7da;border-left:4px solid #dc3545;padding:10px 16px;">Sizing error: {err}</div>'

        # Validation warnings
        warn_html = ""
        for w in results.get("validation_messages", []):
            css = {"ERROR": "background:#f8d7da;border-left:4px solid #dc3545",
                   "WARNING": "background:#fff3cd;border-left:4px solid #ffc107",
                   "INFO": "background:#d1ecf1;border-left:4px solid #17a2b8"}.get(w.level, "")
            icon = {"ERROR": "[ERROR]", "WARNING": "[WARNING]", "INFO": "[INFO]"}.get(w.level, "[INFO]")
            warn_html += f'<div style="{css};padding:8px 14px;margin:4px 0;">{icon} <strong>[{w.level}]</strong> {w.message}</div>'

        html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
        <title>PSV Sizing</title>
        <style>
            body {{ font-family: Arial, sans-serif; font-size: 11pt; margin: 24px; color: #1a1a1a; }}
            h2 {{ color: #003366; border-bottom: 2px solid #003366; padding-bottom: 6px; }}
            h3 {{ color: #336699; margin-top: 20px; }}
            table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
            th {{ background: #003366; color: white; padding: 8px 12px; text-align: left; }}
            td {{ padding: 7px 12px; border: 1px solid #ccc; }}
            tr:nth-child(even) {{ background: #f5f8fc; }}
        </style></head><body>
        <h2>Step 6 — PSV Type Selection and API 520 Sizing</h2>

        <h3>PSV Type Recommendation</h3>
        <div style="background:{type_color};color:white;padding:14px 20px;border-radius:6px;margin:12px 0;">
            <strong style="font-size:14pt">Recommended PSV Type: {psv_type}</strong>
        </div>
        <div style="background:#eef2f7;border-left:4px solid #003366;padding:10px 16px;margin:8px 0;">
            <strong>Engineering Justification:</strong><br/>{psv_just}
        </div>
        <table>
            <thead><tr><th>Parameter</th><th>Value</th></tr></thead>
            <tbody>
                <tr><td style="font-weight:bold;background:#eef2f7">Built-up Backpressure</td>
                    <td>{built_up_pct:.2f}% of set pressure</td></tr>
                <tr><td style="font-weight:bold;background:#eef2f7">Total Backpressure</td>
                    <td>{total_bp_pct:.2f}% of set pressure</td></tr>
                <tr><td style="font-weight:bold;background:#eef2f7">Service</td>
                    <td>{service}</td></tr>
            </tbody>
        </table>

        {sizing_content}

        <h3>Validation and Engineering Warnings</h3>
        {warn_html if warn_html else '<div style="background:#d1ecf1;border-left:4px solid #17a2b8;padding:8px 14px;">No warnings generated.</div>'}

        <p style="font-size:9pt;color:#888;margin-top:20px;">
            Kd values are preliminary. Confirm with PSV manufacturer before final design.
            API 520 Part I, 10th Edition. ASME Section VIII, Division 1.
        </p>
        </body></html>"""
        return vkt.WebResult(html=html)

    # ── Step 7 View: Engineering Report ──────────────────────────────────────
    @vkt.WebView("Engineering Report", duration_guess=5)
    def view_report(self, params, **kwargs) -> vkt.WebResult:
        """Generate and display the full engineering calculation report."""
        try:
            results = _run_full_calculation(params)
            html = self._build_report_html(params, results)
            return vkt.WebResult(html=html)
        except Exception as e:
            logger.warning(f"Report generation error: {e}")
            html = f"""<!DOCTYPE html><html><body style="font-family:Arial;margin:24px;">
            <h2 style="color:#003366;">Engineering Report</h2>
            <div style="background:#f8d7da;border-left:4px solid #dc3545;padding:10px 16px;">
            Report generation error: {e}<br/>
            Please ensure all required inputs are completed in Steps 1 to 6.
            </div></body></html>"""
            return vkt.WebResult(html=html)

    # ── Download Button ───────────────────────────────────────────────────────
    def download_report(self, params, **kwargs) -> vkt.DownloadResult:
        """Generate and download the HTML engineering report."""
        results = _run_full_calculation(params)
        p = params
        report_data = self._build_report_data(p, results)
        html_content = generate_report(report_data)
        tag = p.step1.sec_project.tag_number or "PSV"
        rev = p.step1.sec_project.revision or "0"
        filename = f"PSV_Sizing_{tag}_Rev{rev}.html"
        return vkt.DownloadResult(
            file_content=html_content.encode("utf-8"),
            file_name=filename,
        )

    # ── Internal: Build report data dict ─────────────────────────────────────
    def _build_report_data(self, params, results: dict) -> dict:
        """Assemble the report data dictionary from params and calculation results."""
        p = params
        return {
            "project": {
                "project_name": p.step1.sec_project.project_name,
                "doc_number":   p.step1.sec_project.doc_number,
                "revision":     p.step1.sec_project.revision,
                "prepared_by":  p.step1.sec_project.prepared_by,
                "tag_number":   p.step1.sec_project.tag_number,
            },
            "equipment": {
                "equipment_type":  p.step1.sec_equip.equipment_type,
                "service":         p.step1.sec_equip.service,
                "fluid_name":      p.step1.sec_equip.fluid_name,
                "op_pressure":     p.step1.sec_pressures.op_pressure,
                "op_temp":         p.step1.sec_pressures.op_temp,
                "design_pressure": p.step1.sec_pressures.design_pressure,
                "design_temp":     p.step1.sec_pressures.design_temp,
                "mawp":            p.step1.sec_pressures.mawp,
                "set_pressure":    p.step1.sec_pressures.set_pressure,
                "mw":              p.step1.sec_fluid.mw,
                "k_ratio":         p.step1.sec_fluid.k_ratio,
                "z_factor":        p.step1.sec_fluid.z_factor,
                "viscosity":       p.step1.sec_fluid.viscosity,
                "latent_heat":     p.step1.sec_fluid.latent_heat,
            },
            "scenarios": [
                {"name": s.name, "credible": s.credible, "basis": s.basis, "api_ref": s.api_ref}
                for s in results["scenario_results"]
            ],
            "relieving_conditions": {
                "governing_scenario":      results["governing"]["name"] if results["governing"] else "N/A",
                "set_pressure_barg":       results["relieving_pressure"]["set_pressure_barg"],
                "overpressure_pct":        results["relieving_pressure"]["overpressure_pct"],
                "relieving_pressure_barg": results["relieving_pressure"]["relieving_pressure_barg"],
                "relieving_pressure_psia": results["relieving_pressure"]["relieving_pressure_psia"],
                "accumulation_bar":        results["relieving_pressure"]["accumulation_bar"],
                "relieving_temp_c":        results["relieving_temperature"]["relieving_temp_c"],
                "relieving_temp_r":        results["relieving_temperature"]["relieving_temp_r"],
                "relieving_load_kg_h":     results["governing"]["load_kg_h"] if results["governing"] else 0.0,
                "relieving_load_lb_h":     results["governing"]["load_kg_h"] * 2.20462 if results["governing"] else 0.0,
            },
            "backpressure": results["backpressure"],
            "psv_type":     results["psv_type"],
            "sizing":       results.get("sizing", {}),
            "orifice":      results.get("orifice", {}),
            "warnings": [
                {"level": w.level, "message": w.message}
                for w in results.get("validation_messages", [])
            ],
            "unit_system": p.step1.sec_equip.unit_system,
        }

    # ── Internal: Build full report HTML for WebView ─────────────────────────
    def _build_report_html(self, params, results: dict) -> str:
        """Delegate to report_generator module."""
        return generate_report(self._build_report_data(params, results))

