"""
Module for constructing prompts for LLM interactions in the Diplomacy game.
"""
import logging
from typing import Dict, List, Optional, Any # Added Any for game type placeholder

from .utils import load_prompt
from .possible_order_context import generate_rich_order_context
from .game_history import GameHistory # Assuming GameHistory is correctly importable

# placeholder for diplomacy.Game to avoid circular or direct dependency if not needed for typehinting only
# from diplomacy import Game # Uncomment if 'Game' type hint is crucial and available

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG) # Or inherit from parent logger

def build_context_prompt(
    game: Any, # diplomacy.Game object
    board_state: dict,
    power_name: str,
    possible_orders: Dict[str, List[str]],
    game_history: GameHistory,
    agent_goals: Optional[List[str]] = None,
    agent_relationships: Optional[Dict[str, str]] = None,
    agent_private_diary: Optional[str] = None,
) -> str:
    """Builds the detailed context part of the prompt.

    Args:
        game: The game object.
        board_state: Current state of the board.
        power_name: The name of the power for whom the context is being built.
        possible_orders: Dictionary of possible orders.
        game_history: History of the game (messages, etc.).
        agent_goals: Optional list of agent's goals.
        agent_relationships: Optional dictionary of agent's relationships with other powers.
        agent_private_diary: Optional string of agent's private diary.

    Returns:
        A string containing the formatted context.
    """
    context_template = load_prompt("context_prompt.txt")

    # === Agent State Debug Logging ===
    if agent_goals:
        logger.debug(f"Using goals for {power_name}: {agent_goals}")
    if agent_relationships:
        logger.debug(f"Using relationships for {power_name}: {agent_relationships}")
    if agent_private_diary:
        logger.debug(f"Using private diary for {power_name}: {agent_private_diary[:200]}...")
    # ================================

    # Get our units and centers (not directly used in template, but good for context understanding)
    # units_info = board_state["units"].get(power_name, [])
    # centers_info = board_state["centers"].get(power_name, [])

    # Get the current phase
    year_phase = board_state["phase"]  # e.g. 'S1901M'

    possible_orders_context_str = generate_rich_order_context(game, power_name, possible_orders)

    messages_this_round_text = game_history.get_messages_this_round(
        power_name=power_name,
        current_phase_name=year_phase
    )
    if not messages_this_round_text.strip():
        messages_this_round_text = "\n(No messages this round)\n"

    # Separate active and eliminated powers for clarity
    active_powers = [p for p in game.powers.keys() if not game.powers[p].is_eliminated()]
    eliminated_powers = [p for p in game.powers.keys() if game.powers[p].is_eliminated()]
    
    # Build units representation with power status
    units_lines = []
    for p, u in board_state["units"].items():
        if game.powers[p].is_eliminated():
            units_lines.append(f"  {p}: {u} [ELIMINATED]")
        else:
            units_lines.append(f"  {p}: {u}")
    units_repr = "\n".join(units_lines)
    
    # Build centers representation with power status  
    centers_lines = []
    for p, c in board_state["centers"].items():
        if game.powers[p].is_eliminated():
            centers_lines.append(f"  {p}: {c} [ELIMINATED]")
        else:
            centers_lines.append(f"  {p}: {c}")
    centers_repr = "\n".join(centers_lines)
    
    # Get draw vote history
    draw_vote_summary = game_history.get_draw_vote_summary()
    if not draw_vote_summary or draw_vote_summary == "No draw votes recorded yet":
        draw_vote_history_text = "No draw votes have been called yet."
    else:
        draw_vote_history_text = draw_vote_summary

    context = context_template.format(
        power_name=power_name,
        current_phase=year_phase,
        all_unit_locations=units_repr,
        all_supply_centers=centers_repr,
        messages_this_round=messages_this_round_text,
        possible_orders=possible_orders_context_str,
        agent_goals="\n".join(f"- {g}" for g in agent_goals) if agent_goals else "None specified",
        agent_relationships="\n".join(f"- {p}: {s}" for p, s in agent_relationships.items()) if agent_relationships else "None specified",
        agent_private_diary=agent_private_diary if agent_private_diary else "(No diary entries yet)",
        draw_vote_history=draw_vote_history_text,
    )

    return context

def construct_order_generation_prompt(
    system_prompt: str,
    game: Any, # diplomacy.Game object
    board_state: dict,
    power_name: str,
    possible_orders: Dict[str, List[str]],
    game_history: GameHistory,
    agent_goals: Optional[List[str]] = None,
    agent_relationships: Optional[Dict[str, str]] = None,
    agent_private_diary_str: Optional[str] = None,
) -> str:
    """Constructs the final prompt for order generation.

    Args:
        system_prompt: The base system prompt for the LLM.
        game: The game object.
        board_state: Current state of the board.
        power_name: The name of the power for whom the prompt is being built.
        possible_orders: Dictionary of possible orders.
        game_history: History of the game (messages, etc.).
        agent_goals: Optional list of agent's goals.
        agent_relationships: Optional dictionary of agent's relationships with other powers.
        agent_private_diary_str: Optional string of agent's private diary.

    Returns:
        A string containing the complete prompt for the LLM.
    """
    # Load prompts
    _ = load_prompt("few_shot_example.txt") # Loaded but not used, as per original logic
    instructions = load_prompt("order_instructions.txt")

    # Build the context prompt
    context = build_context_prompt(
        game,
        board_state,
        power_name,
        possible_orders,
        game_history,
        agent_goals=agent_goals,
        agent_relationships=agent_relationships,
        agent_private_diary=agent_private_diary_str,
    )

    final_prompt = system_prompt + "\n\n" + context + "\n\n" + instructions
    return final_prompt
