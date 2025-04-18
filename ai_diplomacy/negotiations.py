from dotenv import load_dotenv
import logging
import concurrent.futures
from typing import Dict, TYPE_CHECKING

from diplomacy.engine.message import Message, GLOBAL

from .agent import DiplomacyAgent
from .clients import load_model_client
from .utils import gather_possible_orders, load_prompt

if TYPE_CHECKING:
    from .game_history import GameHistory
    from diplomacy import Game

logger = logging.getLogger("negotiations")
logger.setLevel(logging.INFO)
logging.basicConfig(level=logging.INFO)

load_dotenv()


def conduct_negotiations(
    game: 'Game',
    agents: Dict[str, DiplomacyAgent],
    game_history: 'GameHistory',
    model_error_stats: Dict[str, Dict[str, int]],
    max_rounds: int = 3,
):
    """
    Conducts a round-robin conversation among all non-eliminated powers.
    Each power can send up to 'max_rounds' messages, choosing between private
    and global messages each turn.
    """
    logger.info("Starting negotiation phase.")

    active_powers = [
        p_name for p_name, p_obj in game.powers.items() if not p_obj.is_eliminated()
    ]

    # We do up to 'max_rounds' single-message turns for each power
    for round_index in range(max_rounds):
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=1
        ) as executor:
            futures = {}
            for power_name in active_powers:
                if power_name not in agents:
                    logger.warning(f"Agent for {power_name} not found in negotiations. Skipping.")
                    continue
                agent = agents[power_name]
                client = agent.client

                possible_orders = gather_possible_orders(game, power_name)
                if not possible_orders:
                    logger.info(f"No orderable locations for {power_name}; skipping.")
                    continue
                board_state = game.get_state()

                future = executor.submit(
                    client.get_conversation_reply,
                    game,
                    board_state,
                    power_name,
                    possible_orders,
                    game_history,
                    game.current_short_phase,
                    active_powers,
                    agent_goals=agent.goals,
                    agent_relationships=agent.relationships,
                )

                futures[future] = power_name
                logger.debug(f"Submitted get_conversation_reply task for {power_name}.")

            for future in concurrent.futures.as_completed(futures):
                power_name = futures[future]
                messages = future.result()
                if messages:
                    for message in messages:
                        # Create an official message in the Diplomacy engine
                        # Determine recipient based on message type
                        if message.get("message_type") == "private":
                            recipient = message.get("recipient", GLOBAL) # Default to GLOBAL if recipient missing somehow
                            if recipient not in game.powers and recipient != GLOBAL:
                                logger.warning(f"Invalid recipient '{recipient}' in message from {power_name}. Sending globally.")
                                recipient = GLOBAL # Fallback to GLOBAL if recipient power is invalid
                        else: # Assume global if not private or type is missing
                            recipient = GLOBAL
                            
                        diplo_message = Message(
                            phase=game.current_short_phase,
                            sender=power_name,
                            recipient=recipient, # Use determined recipient
                            message=message.get("content", ""), # Use .get for safety
                            time_sent=None, # Let the engine assign time
                        )
                        game.add_message(diplo_message)
                        # Also add to our custom history
                        game_history.add_message(
                            game.current_short_phase,
                            power_name,
                            recipient, # Use determined recipient here too
                            message.get("content", ""), # Use .get for safety
                        )
                        journal_recipient = f"to {recipient}" if recipient != GLOBAL else "globally"
                        agent.add_journal_entry(f"Sent message {journal_recipient} in {game.current_short_phase}: {message.get('content', '')[:100]}...")
                else:
                    logger.debug(f"No valid messages returned for {power_name}.")
                    model_error_stats[power_name]["conversation_errors"] += 1

    logger.info("Negotiation phase complete.")
    return game_history
