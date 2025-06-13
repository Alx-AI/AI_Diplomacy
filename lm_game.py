import argparse
import logging
import time
import dotenv
import os
import json
import asyncio
from collections import defaultdict
import concurrent.futures

# Suppress Gemini/PaLM gRPC warnings
os.environ["GRPC_PYTHON_LOG_LEVEL"] = "40"  # ERROR level only
os.environ["GRPC_VERBOSITY"] = "ERROR"  # Additional gRPC verbosity control
os.environ["ABSL_MIN_LOG_LEVEL"] = "2"  # Suppress abseil warnings
# Disable gRPC forking warnings
os.environ["GRPC_POLL_STRATEGY"] = "poll"  # Use 'poll' for macOS compatibility

from diplomacy import Game
from diplomacy.engine.message import GLOBAL, Message
from diplomacy.utils.export import to_saved_game_format

from ai_diplomacy.clients import load_model_client
from ai_diplomacy.utils import (
    get_valid_orders,
    gather_possible_orders,
    assign_models_to_powers,
)
from ai_diplomacy.negotiations import conduct_negotiations
from ai_diplomacy.planning import planning_phase
from ai_diplomacy.game_history import GameHistory
from ai_diplomacy.agent import DiplomacyAgent
import ai_diplomacy.narrative
from ai_diplomacy.initialization import initialize_agent_state_ext

dotenv.load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)
# Silence noisy dependencies
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("root").setLevel(logging.WARNING) # Assuming root handles AFC


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Run a Diplomacy game simulation with configurable parameters."
    )
    parser.add_argument(
        "--max_year",
        type=int,
        default=1901,
        help="Maximum year to simulate. The game will stop once this year is reached.",
    )
    parser.add_argument(
        "--num_negotiation_rounds",
        type=int,
        default=0,
        help="Number of negotiation rounds per phase.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="Output filename for the final JSON result. If not provided, a timestamped name will be generated.",
    )
    parser.add_argument(
        "--models",
        type=str,
        default="",
        help=(
            "Comma-separated list of model names to assign to powers in order. "
            "The order is: AUSTRIA, ENGLAND, FRANCE, GERMANY, ITALY, RUSSIA, TURKEY."
        ),
    )
    parser.add_argument(
        "--planning_phase", 
        action="store_true",
        help="Enable the planning phase for each power to set strategic directives.",
    )
    parser.add_argument(
        "--disable_draw",
        action="store_true",
        help="Disable draw voting functionality. By default, draw voting is enabled.",
    )
    parser.add_argument(
        "--draw_start_year",
        type=int,
        default=1905,
        help="Year when draw voting becomes available (default: 1905).",
    )
    return parser.parse_args()


async def main():
    args = parse_arguments()
    max_year = args.max_year

    logger.info(
        "Starting a new Diplomacy game for testing with multiple LLMs, now async!"
    )
    start_whole = time.time()

    model_error_stats = defaultdict(
        lambda: {"conversation_errors": 0, "order_decoding_errors": 0}
    )

    # Create a fresh Diplomacy game
    game = Game()
    game_history = GameHistory()

    # Ensure game has phase_summaries attribute
    if not hasattr(game, "phase_summaries"):
        game.phase_summaries = {}

    # Determine the result folder based on a timestamp
    timestamp_str = time.strftime("%Y%m%d_%H%M%S")
    result_folder = f"./results/{timestamp_str}"
    os.makedirs(result_folder, exist_ok=True)

    # ADDED: Setup general file logging
    general_log_file_path = os.path.join(result_folder, "general_game.log")
    file_handler = logging.FileHandler(general_log_file_path, mode='a') # Append mode
    file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - [%(funcName)s:%(lineno)d] - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    file_handler.setFormatter(file_formatter)
    # Use the same log_level as basicConfig, or set a different one if needed for the file
    file_handler.setLevel(logging.INFO) 
    logging.getLogger().addHandler(file_handler) # Add handler to the root logger
    
    # It's good practice to define 'logger' after all root logger configurations if it's module-specific.
    # If 'logger = logging.getLogger(__name__)' is defined later, this message will use it.
    # If not, it uses the root logger directly.
    logging.info(f"General game logs will be appended to: {general_log_file_path}")

    # File paths
    manifesto_path = f"{result_folder}/game_manifesto.txt"
    # Use provided output filename or generate one based on the timestamp
    game_file_path = args.output if args.output else f"{result_folder}/lmvsgame.json"
    overview_file_path = f"{result_folder}/overview.jsonl"
    # == Add LLM Response Log Path ==
    llm_log_file_path = f"{result_folder}/llm_responses.csv"

    # Handle power model mapping
    if args.models:
        # Expected order: AUSTRIA, ENGLAND, FRANCE, GERMANY, ITALY, RUSSIA, TURKEY
        powers_order = [
            "AUSTRIA",
            "ENGLAND",
            "FRANCE",
            "GERMANY",
            "ITALY",
            "RUSSIA",
            "TURKEY",
        ]
        provided_models = [name.strip() for name in args.models.split(",")]
        if len(provided_models) != len(powers_order):
            logger.error(
                f"Expected {len(powers_order)} models for --power-models but got {len(provided_models)}. Exiting."
            )
            return
        game.power_model_map = dict(zip(powers_order, provided_models))
    else:
        game.power_model_map = assign_models_to_powers()

    # == Goal 1: Centralize Agent Instances ==
    agents = {}
    initialization_tasks = []
    logger.info("Initializing Diplomacy Agents for each power...")
    for power_name, model_id in game.power_model_map.items():
        if not game.powers[power_name].is_eliminated(): # Only create for active powers initially
            try:
                client = load_model_client(model_id)
                # TODO: Potentially load initial goals/relationships from config later
                agent = DiplomacyAgent(power_name=power_name, client=client) 
                agents[power_name] = agent
                logger.info(f"Preparing initialization task for {power_name} with model {model_id}")
                # Pass log path to initialization
                initialization_tasks.append(initialize_agent_state_ext(agent, game, game_history, llm_log_file_path))
            except Exception as e:
                logger.error(f"Failed to create agent or client for {power_name} with model {model_id}: {e}", exc_info=True)
        else:
             logger.info(f"Skipping agent initialization for eliminated power: {power_name}")
    
    # == Run initializations concurrently ==
    logger.info(f"Running {len(initialization_tasks)} agent initializations concurrently...")
    initialization_results = await asyncio.gather(*initialization_tasks, return_exceptions=True)
    # Check results for errors
    # Note: agents dict might have fewer entries than results if client creation failed
    initialized_powers = list(agents.keys()) # Get powers for which agents were created
    for i, result in enumerate(initialization_results):
         if i < len(initialized_powers): # Ensure index is valid for initialized_powers
             power_name = initialized_powers[i]
             if isinstance(result, Exception):
                 logger.error(f"Failed to initialize agent state for {power_name}: {result}", exc_info=result)
                 # Potentially remove agent if initialization failed? Depends on desired behavior.
             else:
                 logger.info(f"Successfully initialized agent state for {power_name}.")
         else:
             logger.error(f"Initialization result mismatch - unexpected result: {result}")
    # ========================================

    # == Add storage for relationships per phase ==
    all_phase_relationships = {}
    all_phase_relationships_history = {} # Initialize history

    # Log draw voting configuration
    if args.disable_draw:
        logger.info("Draw voting is DISABLED for this game")
    else:
        logger.info(f"Draw voting is ENABLED, starting from year {args.draw_start_year}")

    # Flag to track if game ended by draw
    game_ended_by_draw = False

    while not game.is_game_done:
        phase_start = time.time()
        current_phase = game.get_current_phase()

        # Ensure the current phase is registered in the history
        game_history.add_phase(current_phase)
        
        # Store the current phase's short name once for consistent use
        current_short_phase = game.current_short_phase
        
        logger.info(
            f"PHASE: {current_phase} (time so far: {phase_start - start_whole:.2f}s)"
        )

        # DEBUG: Print the short phase to confirm
        logger.debug(f"DEBUG: current_short_phase is '{current_short_phase}'")

        # Prevent unbounded simulation based on year
        year_str = current_phase[1:5]
        year_int = int(year_str)
        if year_int > max_year:
            logger.info(f"Reached year {year_int}, stopping the test game early.")
            break

        # If it's a movement phase (e.g. ends with "M"), conduct negotiations
        if game.current_short_phase.endswith("M"):
            if args.num_negotiation_rounds > 0:
                logger.info(f"Running {args.num_negotiation_rounds} rounds of negotiations...")
                game_history = await conduct_negotiations(
                    game,
                    agents,
                    game_history,
                    model_error_stats,
                    max_rounds=args.num_negotiation_rounds,
                    # Pass log path
                    log_file_path=llm_log_file_path,
                )
            else:
                logger.info("Skipping negotiation phase as num_negotiation_rounds=0")

            # === Execute Planning Phase (if enabled) AFTER potential negotiations ===
            if args.planning_phase:
                logger.info("Executing strategic planning phase...")
                # NOTE: Assuming planning_phase needs modification to accept log_path
                # We'll modify this call after checking planning.py
                # Pass log path to planning
                await planning_phase(
                    game,
                    agents,
                    game_history,
                    model_error_stats, 
                    log_file_path=llm_log_file_path,
                )
            # ======================================================================

            # === Generate Negotiation Diary Entries ===
            logger.info(f"Generating negotiation diary entries for phase {current_short_phase}...")
            
            # Log active and eliminated powers for negotiation diary
            active_powers_for_neg_diary = [p for p in agents.keys() if not game.powers[p].is_eliminated()]
            eliminated_powers_for_neg_diary = [p for p in agents.keys() if game.powers[p].is_eliminated()]
            
            logger.info(f"Active powers for negotiation diary: {active_powers_for_neg_diary}")
            if eliminated_powers_for_neg_diary:
                logger.info(f"Eliminated powers (skipped): {eliminated_powers_for_neg_diary}")
            
            neg_diary_tasks = []
            for power_name, agent in agents.items():
                if not game.powers[power_name].is_eliminated():
                    neg_diary_tasks.append(
                        agent.generate_negotiation_diary_entry(
                            game,
                            game_history,
                            llm_log_file_path
                        )
                    )
            if neg_diary_tasks:
                await asyncio.gather(*neg_diary_tasks, return_exceptions=True)
            logger.info(f"Finished generating negotiation diary entries for {current_short_phase}.")
            # ==========================================

        # AI Decision Making: Get orders for each power
        logger.info("Getting orders from agents...")
        
        # Log active and eliminated powers for order generation
        active_powers_for_orders = [p for p in agents.keys() if not game.powers[p].is_eliminated()]
        eliminated_powers_for_orders = [p for p in agents.keys() if game.powers[p].is_eliminated()]
        
        logger.info(f"Active powers for order generation: {active_powers_for_orders}")
        if eliminated_powers_for_orders:
            logger.info(f"Eliminated powers (skipped): {eliminated_powers_for_orders}")
        
        order_tasks = []
        order_power_names = []
        # Calculate board state once before the loop
        board_state = game.get_state()

        for power_name, agent in agents.items():
            if game.powers[power_name].is_eliminated():
                logger.debug(f"Skipping order generation for eliminated power {power_name}.")
                continue

            # ADDED: Diagnostic logging for orderable locations
            logger.info(f"--- Diagnostic Log for {power_name} in phase {current_phase} ---")
            try:
                orderable_locs_from_game = game.get_orderable_locations(power_name)
                logger.info(f"[{power_name}][{current_phase}] game.get_orderable_locations(): {orderable_locs_from_game}")
                actual_units = game.get_units(power_name)
                actual_unit_locs = [unit.split(' ')[1].split('/')[0] for unit in actual_units if ' ' in unit] # Corrected parsing
                logger.info(f"[{power_name}][{current_phase}] Actual unit locations (from game.get_units()): {actual_unit_locs}")
            except Exception as e_diag:
                logger.error(f"[{power_name}][{current_phase}] Error during diagnostic logging: {e_diag}")
            logger.info(f"--- End Diagnostic Log for {power_name} in phase {current_phase} ---")

            # Calculate possible orders for the current power
            possible_orders = gather_possible_orders(game, power_name)
            if not possible_orders:
                logger.debug(f"No orderable locations for {power_name}; submitting empty orders.")
                game.set_orders(power_name, []) # Ensure empty orders if none possible
                continue

            order_power_names.append(power_name)
            # NOTE: get_valid_orders is in utils, we assume it calls client.get_orders
            # Need to modify get_valid_orders signature in utils.py later
            
            # Debug logging for diary
            diary_preview = agent.format_private_diary_for_prompt()
            logger.info(f"[{power_name}] Passing diary to get_valid_orders. Preview: {diary_preview[:200]}...")
            
            order_tasks.append(
                get_valid_orders(
                    # --- Positional Arguments --- 
                    game,                    
                    agent.client,            
                    board_state,             
                    power_name,              
                    possible_orders,         
                    game_history,            
                    model_error_stats,       
                    # --- Keyword Arguments --- 
                    agent_goals=agent.goals,
                    agent_relationships=agent.relationships,
                    agent_private_diary_str=diary_preview,  # Fixed: Added missing diary parameter, now using pre-formatted value
                    log_file_path=llm_log_file_path,
                    phase=current_phase,     
                )
            )

        # Run order generation concurrently
        if order_tasks:
            logger.debug(f"Running {len(order_tasks)} order generation tasks concurrently...")
            order_results = await asyncio.gather(*order_tasks, return_exceptions=True)
        else:
            logger.debug("No order generation tasks to run.")
            order_results = []

        # Process order results and set them in the game
        for i, result in enumerate(order_results):
            p_name = order_power_names[i]
            agent = agents[p_name] # Get agent for logging/stats if needed
            model_name = agent.client.model_name

            if isinstance(result, Exception):
                logger.error(f"Error during get_valid_orders for {p_name}: {result}", exc_info=result)
                # Log error stats (consider if fallback orders should be set here)
                if model_name in model_error_stats:
                    model_error_stats[model_name].setdefault("order_generation_errors", 0)
                    model_error_stats[model_name]["order_generation_errors"] += 1
                # Optionally set fallback orders here if needed, e.g., game.set_orders(p_name, []) or specific fallback
                game.set_orders(p_name, []) # Set empty orders on error for now
                logger.warning(f"Setting empty orders for {p_name} due to generation error.")
            elif result is None:
                # Handle case where get_valid_orders might theoretically return None
                logger.warning(f"get_valid_orders returned None for {p_name}. Setting empty orders.")
                game.set_orders(p_name, [])
                if model_name in model_error_stats:
                    model_error_stats[model_name].setdefault("order_generation_errors", 0)
                    model_error_stats[model_name]["order_generation_errors"] += 1
            else:
                # Result is the list of validated orders
                orders = result
                logger.debug(f"Validated orders for {p_name}: {orders}")
                if orders:
                    game.set_orders(p_name, orders)
                    logger.debug(
                        f"Set orders for {p_name} in {game.current_short_phase}: {orders}"
                    )
                    # === Generate Order Diary Entry ===
                    # Call after orders are successfully set
                    logger.info(f"Generating order diary entry for {p_name} for phase {current_short_phase}...")
                    try:
                        await agent.generate_order_diary_entry(
                            game,
                            orders, # Pass the confirmed orders
                            llm_log_file_path
                        )
                        logger.info(f"Finished generating order diary entry for {p_name}.")
                    except Exception as e_diary:
                        logger.error(f"Error generating order diary for {p_name}: {e_diary}", exc_info=True)
                    # =================================
                else:
                    logger.debug(f"No valid orders returned by get_valid_orders for {p_name}. Setting empty orders.")
                    game.set_orders(p_name, []) # Set empty if get_valid_orders returned empty

        # --- End Async Order Generation ---

        # Process orders
        logger.info(f"Processing orders for {current_phase}...")
        
        # Process with a custom summary callback that captures our custom game_history data
        def phase_summary_callback(system_prompt, user_prompt):
            # This will be called by the game engine's _generate_phase_summary method
            # Get messages for this phase from game_history
            current_phase_obj = None
            for phase in game_history.phases:
                if phase.name == current_short_phase:
                    current_phase_obj = phase
                    break
                
            if not current_phase_obj:
                return f"Phase {current_short_phase} Summary: (No game history data available)"
            
            # 1) Gather the current board state, sorted by # of centers
            power_info = []
            for power_name, power in game.powers.items():
                units_list = list(power.units)
                centers_list = list(power.centers)
                power_info.append(
                    (power_name, len(centers_list), units_list, centers_list)
                )
            # Sort by descending # of centers
            power_info.sort(key=lambda x: x[1], reverse=True)

            # 2) Build text lines for the top "Board State Overview"
            top_lines = ["Current Board State (Ordered by SC Count):"]
            for (p_name, sc_count, units, centers) in power_info:
                top_lines.append(
                    f" • {p_name}: {sc_count} centers (needs 18 to win). "
                    f"Units={units} Centers={centers}"
                )

            # 3) Map orders to "successful", "failed", or "other" outcomes
            success_dict = {}
            fail_dict = {}
            other_dict = {}

            orders_dict = game.order_history.get(current_short_phase, {})
            results_for_phase = game.result_history.get(current_short_phase, {})

            for pwr, pwr_orders in orders_dict.items():
                for order_str in pwr_orders:
                    # Extract the unit from the string
                    tokens = order_str.split()
                    if len(tokens) < 3:
                        # Something malformed
                        other_dict.setdefault(pwr, []).append(order_str)
                        continue
                    unit_name = " ".join(tokens[:2])
                    # We retrieve the order results for that unit
                    results_list = results_for_phase.get(unit_name, [])
                    # Check if the results contain e.g. "dislodged", "bounce", "void"
                    # We consider success if the result list is empty or has no negative results
                    if not results_list or all(res not in ["bounce", "void", "no convoy", "cut", "dislodged", "disrupted"] for res in results_list):
                        success_dict.setdefault(pwr, []).append(order_str)
                    elif any(res in ["bounce", "void", "no convoy", "cut", "dislodged", "disrupted"] for res in results_list):
                        fail_dict.setdefault(pwr, []).append(f"{order_str} - {', '.join(str(r) for r in results_list)}")
                    else:
                        other_dict.setdefault(pwr, []).append(order_str)

            # 4) Build textual lists of successful, failed, and "other" moves
            def format_moves_dict(title, moves_dict):
                lines = [title]
                if not moves_dict:
                    lines.append("  None.")
                    return "\n".join(lines)
                for pwr in sorted(moves_dict.keys()):
                    lines.append(f"  {pwr}:")
                    for mv in moves_dict[pwr]:
                        lines.append(f"    {mv}")
                return "\n".join(lines)

            success_section = format_moves_dict("Successful Moves:", success_dict)
            fail_section = format_moves_dict("Unsuccessful Moves:", fail_dict)
            other_section = format_moves_dict("Other / Unclassified Moves:", other_dict)

            # 5) Combine everything into the final summary text
            summary_parts = []
            summary_parts.append("\n".join(top_lines))
            summary_parts.append("\n" + success_section)
            summary_parts.append("\n" + fail_section)
            
            # Only include "Other" section if it has content
            if other_dict:
                summary_parts.append("\n" + other_section)

            return f"Phase {current_short_phase} Summary:\n\n" + "\n".join(summary_parts)
        
        # Process with our custom callback
        game.process(phase_summary_callback=phase_summary_callback)

        # Log the results
        logger.info(f"Results for {current_phase}:")
        for power_name, power in game.powers.items():
            logger.info(f"{power_name}: {power.centers}")

        # Ensure messages from game_history are added to the game's message system
        # This is required for messages to appear in the Messages tab
        for phase in game_history.phases:
            if phase.name == current_short_phase:
                for msg in phase.messages:
                    try:
                        # Only add if not already present (avoid duplicates)
                        if not any(m.sender == msg.sender and 
                                  m.recipient == msg.recipient and 
                                  m.message == msg.content 
                                  for m in game.messages.values()):
                            game.add_message(Message(
                                phase=current_short_phase,
                                sender=msg.sender,
                                recipient=msg.recipient,
                                message=msg.content,
                                time_sent=int(time.time())
                            ))
                    except Exception as e:
                        logger.warning(f"Could not add message to game: {e}")

        # Add orders to game history
        for power_name in game.order_history[current_short_phase]:
            orders = game.order_history[current_short_phase][power_name]
            results = []
            for order in orders:
                # Example move: "A PAR H" -> unit="A PAR", order_part="H"
                tokens = order.split(" ", 2)
                if len(tokens) < 3:
                    continue
                unit = " ".join(tokens[:2])  # e.g. "A PAR"
                order_part = tokens[2]  # e.g. "H" or "S A MAR"
                results.append(
                    [str(x) for x in game.result_history[current_short_phase][unit]]
                )
            game_history.add_orders(
                current_short_phase,
                power_name,
                game.order_history[current_short_phase][power_name],
            )

        logger.info(f"--- Orders Submitted for {current_phase} ---")
        for power, orders in game.order_history.get(current_short_phase, {}).items():
            order_str = ", ".join(orders) if orders else "(No orders/NOP)"
            logger.info(f"  {power:<8}: {order_str}")
        logger.info("-----------------------------------")

        # == Collect Agent Relationships for this Phase ==
        current_relationships_for_phase = {}
        logger.debug(f"Collecting relationships for phase: {current_short_phase}")
        active_powers_in_phase = set(game.powers.keys()) # Get powers present at end of phase
        for power_name, agent in agents.items():
            # Only collect relationships if the power is still active in the game
            if power_name in active_powers_in_phase and not game.powers[power_name].is_eliminated():
                try:
                    current_relationships_for_phase[power_name] = agent.relationships
                    logger.debug(f"  Collected relationships for {power_name}")
                except Exception as e:
                     logger.error(f"Error getting relationships for {power_name}: {e}")
            # else:
            #    logger.debug(f"  Skipping relationships for inactive/eliminated power {power_name}")
        all_phase_relationships[current_short_phase] = current_relationships_for_phase
        logger.debug(f"Stored relationships for {len(current_relationships_for_phase)} agents in phase {current_short_phase}")
        # ================================================

        # Log phase duration
        phase_end = time.time()
        logger.info(f"Phase {current_phase} took {phase_end - phase_start:.2f}s")

        # --- Generate Phase Result Diary Entries ---
        # This happens after processing but before state updates
        completed_phase_name = current_phase
        logger.info(f"Generating phase result diary entries for completed phase {completed_phase_name}...")
        
        # Log active and eliminated powers for phase result diary
        active_powers_for_phase_diary = [p for p in agents.keys() if not game.powers[p].is_eliminated()]
        eliminated_powers_for_phase_diary = [p for p in agents.keys() if game.powers[p].is_eliminated()]
        
        logger.info(f"Active powers for phase result diary: {active_powers_for_phase_diary}")
        if eliminated_powers_for_phase_diary:
            logger.info(f"Eliminated powers (skipped): {eliminated_powers_for_phase_diary}")
        
        # Get phase summary and all orders for this phase
        phase_summary = game.phase_summaries.get(current_phase, "(Summary not generated)")
        all_orders_this_phase = game.order_history.get(current_short_phase, {})
        
        # Generate diary entries concurrently for all active agents
        phase_result_diary_tasks = []
        for power_name, agent in agents.items():
            if not game.powers[power_name].is_eliminated():
                phase_result_diary_tasks.append(
                    agent.generate_phase_result_diary_entry(
                        game,
                        game_history,
                        phase_summary,
                        all_orders_this_phase,
                        llm_log_file_path
                    )
                )
        
        if phase_result_diary_tasks:
            logger.info(f"Running {len(phase_result_diary_tasks)} phase result diary tasks concurrently...")
            await asyncio.gather(*phase_result_diary_tasks, return_exceptions=True)
            logger.info(f"Finished generating phase result diary entries.")
        # --- End Phase Result Diary Generation ---

        # --- Diary Consolidation Check ---
        # After processing S1903M (or later), consolidate diary entries from 2 years ago
        logger.info(f"[DIARY CONSOLIDATION] Checking consolidation for phase: {current_phase} (short: {current_short_phase})")
        
        # Try extracting from both phase formats to be safe
        if len(current_short_phase) >= 6:  # e.g., "S1903M"
            current_year_str = current_short_phase[1:5]
            logger.info(f"[DIARY CONSOLIDATION] Extracting year from short phase: {current_short_phase}")
        else:
            current_year_str = current_phase[1:5]  # Extract year from phase
            logger.info(f"[DIARY CONSOLIDATION] Extracting year from full phase: {current_phase}")
            
        logger.info(f"[DIARY CONSOLIDATION] Extracted year string: '{current_year_str}'")
        
        try:
            current_year = int(current_year_str)
            consolidation_year = current_year - 2  # Two years ago
            logger.info(f"[DIARY CONSOLIDATION] Current year: {current_year}, Consolidation year: {consolidation_year}")
            logger.info(f"[DIARY CONSOLIDATION] Phase check - ends with 'M': {current_short_phase.endswith('M')}, starts with 'S': {current_short_phase.startswith('S')}")
            logger.info(f"[DIARY CONSOLIDATION] Consolidation year check: {consolidation_year} >= 1901: {consolidation_year >= 1901}")
            
            # Check if we need to consolidate (after spring movement phase)
            if current_short_phase.endswith("M") and current_short_phase.startswith("S") and consolidation_year >= 1901:
                logger.info(f"[DIARY CONSOLIDATION] TRIGGERING consolidation for year {consolidation_year} (current year: {current_year})")
                
                # Log active and eliminated powers for diary consolidation
                active_powers_for_consolidation = [p for p in agents.keys() if not game.powers[p].is_eliminated()]
                eliminated_powers_for_consolidation = [p for p in agents.keys() if game.powers[p].is_eliminated()]
                
                logger.info(f"[DIARY CONSOLIDATION] Active powers for consolidation: {active_powers_for_consolidation}")
                if eliminated_powers_for_consolidation:
                    logger.info(f"[DIARY CONSOLIDATION] Eliminated powers (skipped): {eliminated_powers_for_consolidation}")
                
                consolidation_tasks = []
                for power_name, agent in agents.items():
                    if not game.powers[power_name].is_eliminated():
                        logger.info(f"[DIARY CONSOLIDATION] Adding consolidation task for {power_name}")
                        consolidation_tasks.append(
                            agent.consolidate_year_diary_entries(
                                str(consolidation_year),
                                game,
                                llm_log_file_path
                            )
                        )
                    else:
                        logger.info(f"[DIARY CONSOLIDATION] Skipping eliminated power: {power_name}")
                
                if consolidation_tasks:
                    logger.info(f"[DIARY CONSOLIDATION] Running {len(consolidation_tasks)} diary consolidation tasks...")
                    await asyncio.gather(*consolidation_tasks, return_exceptions=True)
                    logger.info("[DIARY CONSOLIDATION] Diary consolidation complete")
                else:
                    logger.warning("[DIARY CONSOLIDATION] No consolidation tasks to run")
            else:
                logger.info(f"[DIARY CONSOLIDATION] Conditions not met for consolidation - phase: {current_short_phase}, year: {current_year}")
        except (ValueError, IndexError) as e:
            logger.error(f"[DIARY CONSOLIDATION] ERROR: Could not parse year from phase {current_phase}: {e}")
        except Exception as e:
            logger.error(f"[DIARY CONSOLIDATION] UNEXPECTED ERROR: {e}", exc_info=True)
        # --- End Diary Consolidation ---

        # --- Async State Update --- 
        logger.info(f"Starting state update analysis for completed phase {completed_phase_name}...")
        
        # Phase summary is already retrieved above
        if f"Summary for {current_phase} not found" in phase_summary:
             logger.warning(phase_summary)

        current_board_state = game.get_state() # State *after* processing

        # Update state concurrently for active agents
        active_agent_powers = [p for p in game.powers.items() if p[0] in agents and not p[1].is_eliminated()] # Filter for powers with active agents AND not eliminated
        
        # Log active and eliminated powers for state updates
        active_powers_for_state_update = [p[0] for p in active_agent_powers]
        eliminated_powers_for_state_update = [p for p in agents.keys() if game.powers[p].is_eliminated()]
        
        logger.info(f"Active powers for state update: {active_powers_for_state_update}")
        if eliminated_powers_for_state_update:
            logger.info(f"Eliminated powers (skipped): {eliminated_powers_for_state_update}")

        if active_agent_powers: # Only run if there are agents to update
             logger.info(f"Beginning concurrent state analysis for {len(active_agent_powers)} agents...")
             
             state_update_tasks = []
             power_names_for_analysis = []

             for power_name, _ in active_agent_powers:
                  agent = agents[power_name]
                  logger.debug(f"Preparing state analysis task for {power_name}")
                  # Append the awaitable call
                  state_update_tasks.append(
                       agent.analyze_phase_and_update_state(
                            game, 
                            current_board_state, # Use state AFTER processing
                            phase_summary, 
                            game_history,
                            llm_log_file_path,
                       )
                  )
                  power_names_for_analysis.append(power_name)
                  
             # Run analysis tasks concurrently
             if state_update_tasks:
                  logger.debug(f"Running {len(state_update_tasks)} state analysis tasks concurrently...")
                  analysis_results = await asyncio.gather(*state_update_tasks, return_exceptions=True)
             else:
                  analysis_results = []
                  
             # Process results (check for exceptions)
             for i, result in enumerate(analysis_results):
                 power_name = power_names_for_analysis[i]
                 if isinstance(result, Exception):
                      logger.error(f"Error during state analysis for {power_name}: {result}", exc_info=result)
                      # Optionally log error stats here
                 else:
                      # Result is None if the function completes normally
                      logger.debug(f"State analysis completed successfully for {power_name}.")

             # === Populate relationship history for the completed phase ===
             current_phase_name_for_history = completed_phase_name # Or game.current_short_phase if more appropriate
             all_phase_relationships_history[current_phase_name_for_history] = {}
             for power_name, agent_obj in agents.items():
                 all_phase_relationships_history[current_phase_name_for_history][power_name] = agent_obj.relationships.copy()
             logger.info(f"Recorded relationships for phase {current_phase_name_for_history} into history.")
             # ==========================================================

             logger.info(f"Finished concurrent state analysis for {len(active_agent_powers)} agents.")
             logger.info(f"Completed state update analysis for phase {completed_phase_name}.")
        else:
             logger.info(f"No active agents found to perform state update analysis for phase {completed_phase_name}.")
        # --- End Async State Update ---

        # --- Draw Voting Phase ---
        # Only evaluate draws after movement phases and if the game is in a suitable year
        # Extract year safely
        year_int = 1901  # Default
        try:
            if len(current_phase) >= 5 and current_phase[1:5].isdigit():
                year_str = current_phase[1:5]
                year_int = int(year_str)
            else:
                logger.debug(f"Skipping draw vote - phase format not standard: {current_phase}")
        except Exception as e:
            logger.warning(f"Could not extract year from phase {current_phase}: {e}")
            
        if not args.disable_draw and current_short_phase.endswith('M') and year_int >= args.draw_start_year and game.get_current_phase() != 'COMPLETED':
            logger.info(f"Initiating draw voting evaluation for phase {current_short_phase}")
            
            # Collect draw votes from all active powers
            draw_voting_tasks = []
            power_names_for_voting = []
            
            for power_name, agent in agents.items():
                if not game.powers[power_name].is_eliminated():
                    draw_voting_tasks.append(agent.evaluate_draw_decision(game, game_history, llm_log_file_path))
                    power_names_for_voting.append(power_name)
                    
            if draw_voting_tasks:
                logger.info(f"Collecting draw votes from {len(draw_voting_tasks)} active powers...")
                draw_votes = await asyncio.gather(*draw_voting_tasks, return_exceptions=True)
                
                # Submit votes to the game
                for i, vote_result in enumerate(draw_votes):
                    power_name = power_names_for_voting[i]
                    if isinstance(vote_result, Exception):
                        logger.error(f"Error getting draw vote from {power_name}: {vote_result}")
                        vote_decision = 'neutral'
                    else:
                        vote_decision = vote_result
                    
                    # Record vote in game history
                    game_history.add_draw_vote(current_short_phase, power_name, vote_decision)
                    
                    # Submit the vote if this is a network game
                    try:
                        if hasattr(game, 'vote'):
                            logger.info(f"{power_name} voting '{vote_decision}' on draw proposal")
                            game.vote(vote=vote_decision, power_name=power_name)
                        else:
                            logger.debug(f"Game does not support voting (non-network game)")
                    except Exception as e:
                        logger.error(f"Error submitting vote for {power_name}: {e}")
                
                # Log voting summary
                yes_votes = sum(1 for v in draw_votes if v == 'yes')
                no_votes = sum(1 for v in draw_votes if v == 'no')
                neutral_votes = sum(1 for v in draw_votes if v == 'neutral')
                logger.info(f"Draw voting complete - YES: {yes_votes}, NO: {no_votes}, NEUTRAL: {neutral_votes}")
                
                # Create detailed voting record
                voting_record = {}
                for i, power_name in enumerate(power_names_for_voting):
                    if i < len(draw_votes) and not isinstance(draw_votes[i], Exception):
                        voting_record[power_name] = draw_votes[i]
                    else:
                        voting_record[power_name] = 'error'
                
                # Add voting info to game history with detailed breakdown
                game_history.add_strategic_directive(
                    current_short_phase,
                    'DRAW_VOTING',
                    f"Draw votes - YES: {yes_votes}, NO: {no_votes}, NEUTRAL: {neutral_votes} | Details: {json.dumps(voting_record)}"
                )
                
                # Check if draw would be successful (all non-eliminated powers voted yes)
                if yes_votes == len(draw_votes) and yes_votes > 0:
                    logger.info(f"DRAW CONDITION MET - All {yes_votes} active powers voted YES")
                    logger.info("Game will end in a draw after this phase.")
                    
                    # Add a final message to game history
                    game_history.add_message(
                        phase_name=current_short_phase,
                        sender='GAME',
                        recipient='ALL',
                        message_content=f"Game ended by unanimous draw vote. All {yes_votes} surviving powers agreed to draw."
                    )
                    
                    # Get all surviving powers
                    surviving_powers = [p for p in game.powers.keys() if not game.powers[p].is_eliminated()]
                    
                    # Call the draw method to properly end the game
                    game.draw(winners=surviving_powers)
                    
                    # Mark that the game should end after this phase
                    game_ended_by_draw = True
                    
                elif yes_votes > 0:
                    logger.info(f"Draw proposal failed - needed {len(draw_votes)} YES votes, got {yes_votes}")
        elif args.disable_draw and current_short_phase.endswith('M') and year_int >= args.draw_start_year:
            logger.debug(f"Draw voting is disabled, skipping evaluation for phase {current_short_phase}")
        # --- End Draw Voting Phase ---

        # Append the strategic directives to the manifesto file
        strategic_directives = game_history.get_strategic_directives()
        if strategic_directives:
            out_str = f"Strategic directives for {current_phase}:\n"
            for power, directive in strategic_directives.items():
                out_str += f"{power}: {directive}\n\n"
            out_str += f"------------------------------------------\n"
            with open(manifesto_path, "a") as f:
                f.write(out_str)

        # Check if game ended by draw
        if 'game_ended_by_draw' in locals() and game_ended_by_draw:
            logger.info("Breaking game loop - game ended by unanimous draw vote")
            break

        # Check if we've exceeded the max year
        if year_int > max_year:
            logger.info(f"Reached year {year_int}, stopping the test game early.")
            break

    # Game is done
    total_time = time.time() - start_whole
    if game_ended_by_draw:
        logger.info(f"Game ended by DRAW after {total_time:.2f}s. Saving results...")
        # The draw() method should have been called, which sets the outcome
        # If not, we can call it now
        if not game.outcome:
            # Get all surviving powers
            surviving_powers = [p for p in game.powers.keys() if not game.powers[p].is_eliminated()]
            game.draw(winners=surviving_powers)
    else:
        logger.info(f"Game ended after {total_time:.2f}s. Saving results...")

    # Now save the game with our added data
    output_path = game_file_path
    # If the file already exists, append a timestamp to the filename
    if os.path.exists(output_path):
        logger.info("Game file already exists, saving with unique filename.")
        timestamp = int(time.time())
        base, ext = os.path.splitext(output_path)
        output_path = f"{base}_{timestamp}{ext}"

    # Generate the saved game JSON using the standard export function
    saved_game = to_saved_game_format(game)
    
    # Add draw outcome if applicable
    if game_ended_by_draw:
        # The game.outcome should already be set by the draw() method
        # Format is: [phase_abbr, victor1, victor2, ...]
        if game.outcome:
            saved_game['draw_participants'] = game.outcome[1:]  # Skip the phase abbreviation
            saved_game['draw_reason'] = 'Unanimous draw vote by all surviving powers'
            logger.info(f"Game ended in draw. Participants: {saved_game['draw_participants']}")
        else:
            logger.warning("Draw was supposed to happen but game.outcome is not set")
    
    # Verify phase_summaries are available in game.phase_summaries
    logger.info(f"Game has {len(game.phase_summaries)} phase summaries: {list(game.phase_summaries.keys())}")

    # CRITICAL: Add phase_summaries as a top-level property in the saved game
    # The frontend expects this exact structure
    saved_game['phase_summaries'] = game.phase_summaries
    logger.info(f"Added phase_summaries to saved game with {len(game.phase_summaries)} phases")

    # Also add summaries to individual phases for backward compatibility
    summary_phases_count = 0
    for i, phase in enumerate(saved_game['phases']):
        phase_name = phase['name']
        if phase_name in game.phase_summaries:
            saved_game['phases'][i]['summary'] = game.phase_summaries[phase_name]
            summary_phases_count += 1
            logger.debug(f"Added summary to phase {phase_name} in export")
    logger.info(f"Added summaries to {summary_phases_count}/{len(saved_game['phases'])} phases in the export")

    # == Capture Final Agent States After All Updates ==
    final_agent_states = {}
    if agents: # Ensure agents exist
        for power_name, agent in agents.items():
            # Access attributes directly as they should exist on the agent object
            final_agent_states[power_name] = {
                "relationships": agent.relationships,
                "goals": agent.goals,
                # Optionally add last diary entry or other final state info here
            }
        logger.info(f"Captured final states for {len(final_agent_states)} agents.")
        # Add this dictionary to the main saved_game object
        saved_game['final_agent_states'] = final_agent_states
        logger.info("Added 'final_agent_states' key to the saved game data.")
    else:
        logger.info("No agents found, skipping capture of final agent states.")
        saved_game['final_agent_states'] = {} # Add empty dict for consistency

    # == Add Agent Relationships to Each Phase in the Export (Using History) ==
    relationships_added_to_phases_count = 0
    for i, phase_data in enumerate(saved_game.get('phases', [])):
        phase_name = phase_data.get('name')
        if phase_name in all_phase_relationships_history:
            saved_game['phases'][i]['agent_relationships'] = all_phase_relationships_history[phase_name]
            relationships_added_to_phases_count += 1
            logger.debug(f"Added agent_relationships from history to phase {phase_name} in export")
    logger.info(f"Added agent_relationships from history to {relationships_added_to_phases_count}/{len(saved_game.get('phases', []))} phases in the export")
    # ======================================================================

    # Save the modified game data
    logger.info(f"Saving game to {output_path}...")
    with open(output_path, "w") as f:
        json.dump(saved_game, f, indent=4)

    # Dump error stats and power model mapping to the overview file
    with open(overview_file_path, "w") as overview_file:
        overview_file.write(json.dumps(model_error_stats) + "\n")
        overview_file.write(json.dumps(game.power_model_map) + "\n")
        overview_file.write(json.dumps(vars(args)) + "\n")

    logger.info(f"Saved game data, manifesto, and error stats in: {result_folder}")
    logger.info("Done.")


if __name__ == "__main__":
    # Run the async main function
    asyncio.run(main())
