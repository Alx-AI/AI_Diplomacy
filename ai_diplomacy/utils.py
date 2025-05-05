from dotenv import load_dotenv
import logging
import os
from typing import Dict, List, Tuple, Set, Optional
from diplomacy import Game
import csv
from typing import TYPE_CHECKING

# Avoid circular import for type hinting
if TYPE_CHECKING:
    from .clients import BaseModelClient

logger = logging.getLogger("utils")
logger.setLevel(logging.INFO)
logging.basicConfig(level=logging.INFO)

load_dotenv()


def assign_models_to_powers() -> Dict[str, str]:
    """
    Example usage: define which model each power uses.
    Return a dict: { power_name: model_id, ... }
    POWERS = ['AUSTRIA', 'ENGLAND', 'FRANCE', 'GERMANY', 'ITALY', 'RUSSIA', 'TURKEY']
    Models supported: o3-mini, o4-mini, o3, gpt-4o, gpt-4o-mini
                    claude-3-5-haiku-20241022, claude-3-5-sonnet-20241022, claude-3-7-sonnet-20250219 
                    gemini-2.0-flash, gemini-2.5-flash-preview-04-17, gemini-2.5-pro-preview-03-25, 
                    deepseek-chat, deepseek-reasoner
                    openrouter-meta-llama/llama-3.3-70b-instruct, openrouter-qwen/qwen3-235b-a22b, openrouter-microsoft/phi-4-reasoning-plus:free, openrouter-deepseek/deepseek-prover-v2:free, openrouter-meta-llama/llama-4-maverick:free, openrouter-nvidia/llama-3.3-nemotron-super-49b-v1:free, openrouter-google/gemma-3-12b-it:free
    """

    return {
        "AUSTRIA": "openrouter-meta-llama/llama-3.3-70b-instruct",
        "ENGLAND": "openrouter-qwen/qwen3-235b-a22b",
        "FRANCE": "openrouter-microsoft/phi-4-reasoning-plus:free",
        "GERMANY": "openrouter-deepseek/deepseek-prover-v2:free",
        "ITALY": "openrouter-meta-llama/llama-4-maverick:free",
        "RUSSIA": "openrouter-nvidia/llama-3.3-nemotron-super-49b-v1:free",
        "TURKEY": "openrouter-google/gemma-3-12b-it:free",
    }


def gather_possible_orders(game: Game, power_name: str) -> Dict[str, List[str]]:
    """
    Returns a dictionary mapping each orderable location to the list of valid orders.
    """
    orderable_locs = game.get_orderable_locations(power_name)
    all_possible = game.get_all_possible_orders()

    result = {}
    for loc in orderable_locs:
        result[loc] = all_possible.get(loc, [])
    return result


async def get_valid_orders(
    game: Game,
    client,
    board_state,
    power_name: str,
    possible_orders: Dict[str, List[str]],
    game_history,
    model_error_stats: Dict[str, Dict[str, int]],
    agent_goals: Optional[List[str]] = None,
    agent_relationships: Optional[Dict[str, str]] = None,
    log_file_path: str = None,
    phase: str = None,
) -> List[str]:
    """
    Tries up to 'max_retries' to generate and validate orders.
    If invalid, we append the error feedback to the conversation
    context for the next retry. If still invalid, return fallback.
    """

    # Ask the LLM for orders
    orders = await client.get_orders(
        game=game,
        board_state=board_state,
        power_name=power_name,
        possible_orders=possible_orders,
        conversation_text=game_history,
        model_error_stats=model_error_stats,
        agent_goals=agent_goals,
        agent_relationships=agent_relationships,
        log_file_path=log_file_path,
        phase=phase,
    )
    
    # Initialize list to track invalid order information
    invalid_info = []
    
    # Validate each order
    all_valid = True
    valid_orders = []
    
    for move in orders:
        # Skip empty orders
        if not move or move.strip() == "":
            continue
            
        # Handle special case for WAIVE
        if move.upper() == "WAIVE":
            valid_orders.append(move)
            continue
            
        # Example move: "A PAR H" -> unit="A PAR", order_part="H"
        tokens = move.split(" ", 2)
        if len(tokens) < 3:
            invalid_info.append(f"Order '{move}' is malformed; expected 'A PAR H' style.")
            all_valid = False
            continue
            
        unit = " ".join(tokens[:2])  # e.g. "A PAR"
        order_part = tokens[2]  # e.g. "H" or "S A MAR"

        # Use the internal game validation method
        if order_part == "B":
            validity = 1  # hack because game._valid_order doesn't support 'B'
        else:
            try:
                validity = game._valid_order(
                    game.powers[power_name], unit, order_part, report=1
                )
            except Exception as e:
                logger.warning(f"Error validating order '{move}': {e}")
                invalid_info.append(f"Order '{move}' caused an error: {e}")
                validity = 0
                all_valid = False

        if validity == 1:
            valid_orders.append(move)
        else:
            invalid_info.append(f"Order '{move}' is invalid for {power_name}")
            all_valid = False
    
    # Log validation results
    if invalid_info:
        logger.debug(f"[{power_name}] Invalid orders: {', '.join(invalid_info)}")
    
    if all_valid and valid_orders:
        logger.debug(f"[{power_name}] All orders valid: {valid_orders}")
        return valid_orders
    else:
        logger.debug(f"[{power_name}] Some orders invalid, using fallback.")
        model_error_stats[power_name]["order_decoding_errors"] += 1
        fallback = client.fallback_orders(possible_orders)
        return fallback


def normalize_and_compare_orders(
    issued_orders: Dict[str, List[str]],
    accepted_orders_dict: Dict[str, List[str]],
    game: Game,
) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
    """
    Normalizes and compares issued orders against accepted orders from the game engine.
    Uses the map's built-in normalization methods to ensure consistent formatting.

    Args:
        issued_orders: Dictionary of orders issued by power {power_name: [orders]}
        accepted_orders_dict: Dictionary of orders accepted by the engine,
                              typically from game.get_state()["orders"].
        game: The current Game object containing the map.

    Returns:
        Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]: (orders_not_accepted, orders_not_issued)
            - orders_not_accepted: Orders issued but not accepted by engine (normalized).
            - orders_not_issued: Orders accepted by engine but not issued (normalized).
    """
    game_map = game.map

    def normalize_order(order: str) -> str:
        # Inner function to normalize a single order string using the game map.
        if not order:
            return order

        try:
            # Use map's normalization methods directly
            normalized = game_map.norm(order)
            # Further split and normalize parts for complex orders if necessary
            # (This part might need refinement depending on how complex orders are handled
            #  and represented after initial normalization by game_map.norm)

            # Example (simplified, game_map.norm often handles this):
            # Split support orders
            # parts = normalized.split(" S ")
            # normalized_parts = []
            # for part in parts:
            #     move_parts = part.split(" - ")
            #     move_parts = [game_map.norm(p.strip()) for p in move_parts]
            #     move_parts = [game_map.aliases.get(p, p) for p in move_parts]
            #     normalized_parts.append(" - ".join(move_parts))
            # return " S ".join(normalized_parts)

            return normalized  # Return the directly normalized string for now
        except Exception as e:
            logger.warning(f"Could not normalize order '{order}': {e}")
            return order  # Return original if normalization fails

    orders_not_accepted = {}
    orders_not_issued = {}

    all_powers = set(issued_orders.keys()) | set(accepted_orders_dict.keys())

    for pwr in all_powers:
        # Normalize issued orders for the power, handling potential absence
        issued_set = set()
        if pwr in issued_orders:
            try:
                issued_set = {normalize_order(o) for o in issued_orders.get(pwr, []) if o}
            except Exception as e:
                logger.error(f"Error normalizing issued orders for {pwr}: {e}")

        # Normalize accepted orders for the power, handling potential absence
        accepted_set = set()
        if pwr in accepted_orders_dict:
            try:
                accepted_set = {normalize_order(o) for o in accepted_orders_dict.get(pwr, []) if o}
            except Exception as e:
                logger.error(f"Error normalizing accepted orders for {pwr}: {e}")

        # Compare the sets
        missing_from_engine = issued_set - accepted_set
        missing_from_issued = accepted_set - issued_set

        if missing_from_engine:
            orders_not_accepted[pwr] = missing_from_engine
        if missing_from_issued:
            orders_not_issued[pwr] = missing_from_issued

    return orders_not_accepted, orders_not_issued


# Helper to load prompt text from file relative to the expected 'prompts' dir
def load_prompt(filename: str) -> str:
    """Helper to load prompt text from file"""
    # Assuming execution from the root or that the path resolves correctly
    # Consider using absolute paths or pkg_resources if needed for robustness
    prompt_path = os.path.join(os.path.dirname(__file__), 'prompts', filename)
    try:
        with open(prompt_path, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        logger.error(f"Prompt file not found: {prompt_path}")
        # Return an empty string or raise an error, depending on desired handling
        return ""


# == New LLM Response Logging Function ==
def log_llm_response(
    log_file_path: str,
    model_name: str,
    power_name: Optional[str], # Optional for non-power-specific calls like summary
    phase: str,
    response_type: str,
    raw_response: str,
):
    """Appends a raw LLM response to a CSV log file."""
    try:
        # Ensure the directory exists
        log_dir = os.path.dirname(log_file_path)
        if log_dir: # Ensure log_dir is not empty (e.g., if path is just a filename)
             os.makedirs(log_dir, exist_ok=True)

        # Check if file exists to write header
        file_exists = os.path.isfile(log_file_path)

        with open(log_file_path, "a", newline="", encoding="utf-8") as csvfile:
            fieldnames = ["model", "power", "phase", "response_type", "raw_response"]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

            if not file_exists:
                writer.writeheader()  # Write header only if file is new

            writer.writerow({
                "model": model_name,
                "power": power_name if power_name else "game", # Use 'game' if no specific power
                "phase": phase,
                "response_type": response_type,
                "raw_response": raw_response,
            })
    except Exception as e:
        logger.error(f"Failed to log LLM response to {log_file_path}: {e}", exc_info=True)


# == New Async LLM Wrapper with Logging ==
async def run_llm_and_log(
    client: 'BaseModelClient',
    prompt: str,
    log_file_path: str,
    power_name: Optional[str],
    phase: str,
    response_type: str,
) -> str:
    """Calls the client's generate_response and logs the raw output."""
    raw_response = "" # Initialize in case of error
    try:
        raw_response = await client.generate_response(prompt)
        # Log the successful response
        log_llm_response(
            log_file_path=log_file_path,
            model_name=client.model_name,
            power_name=power_name,
            phase=phase,
            response_type=response_type,
            raw_response=raw_response,
        )
    except Exception as e:
         # Log the error attempt (optional, could log empty response instead)
        logger.error(f"Error during LLM call for {power_name}/{response_type} in phase {phase}: {e}", exc_info=True)
        log_llm_response(
            log_file_path=log_file_path,
            model_name=client.model_name,
            power_name=power_name,
            phase=phase,
            response_type=f"ERROR_{response_type}", # Mark response type as error
            raw_response=f"Error generating response: {e}",
        )
        # Depending on desired behavior, you might want to re-raise the exception
        # or return a specific error indicator string. Returning empty for now.
        # raise e
    return raw_response
