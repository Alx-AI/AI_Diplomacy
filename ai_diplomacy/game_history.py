from dotenv import load_dotenv
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("utils")
logger.setLevel(logging.INFO)
logging.basicConfig(level=logging.INFO)
load_dotenv()


@dataclass
class Message:
    sender: str
    recipient: str
    content: str


@dataclass
class Phase:
    name: str  # e.g. "SPRING 1901"
    plans: Dict[str, str] = field(default_factory=dict)
    messages: List[Message] = field(default_factory=list)
    orders_by_power: Dict[str, List[str]] = field(
        default_factory=lambda: defaultdict(list)
    )
    results_by_power: Dict[str, List[List[str]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    # NEW: Store phase-end summaries provided by each power
    phase_summaries: Dict[str, str] = field(default_factory=dict)
    # NEW: Store experience/journal updates from each power for this phase
    experience_updates: Dict[str, str] = field(default_factory=dict)
    # NEW: Store draw votes for this phase
    draw_votes: Dict[str, str] = field(default_factory=dict)  # power -> vote (yes/no/neutral)
    draw_vote_reasoning: Dict[str, str] = field(default_factory=dict)  # power -> reasoning

    def add_plan(self, power_name: str, plan: str):
        self.plans[power_name] = plan
    
    def add_message(self, sender: str, recipient: str, content: str):
        self.messages.append(
            Message(sender=sender, recipient=recipient, content=content)
        )

    def add_orders(self, power: str, orders: List[str], results: List[List[str]]):
        self.orders_by_power[power].extend(orders)
        # Make sure results has the same length as orders, if not, pad with empty lists
        if len(results) < len(orders):
            results.extend([[] for _ in range(len(orders) - len(results))])
        self.results_by_power[power].extend(results)

    def get_global_messages(self) -> str:
        result = ""
        for msg in self.messages:
            if msg.recipient == "GLOBAL":
                result += f" {msg.sender}: {msg.content}\n"
        return result

    def get_private_messages(self, power: str) -> Dict[str, str]:
        conversations = defaultdict(str)
        for msg in self.messages:
            if msg.sender == power and msg.recipient != "GLOBAL":
                conversations[msg.recipient] += f"  {power}: {msg.content}\n"
            elif msg.recipient == power:
                conversations[msg.sender] += f"  {msg.sender}: {msg.content}\n"
        return conversations

    def get_all_orders_formatted(self) -> str:
        if not self.orders_by_power:
            return ""

        result = f"\nOrders for {self.name}:\n"
        for power, orders in self.orders_by_power.items():
            result += f"{power}:\n"
            results = self.results_by_power.get(power, [])
            for i, order in enumerate(orders):
                if i < len(results) and results[i]:
                    # Join multiple results with commas
                    result_str = f" ({', '.join(results[i])})"
                else:
                    result_str = " (successful)"
                result += f"  {order}{result_str}\n"
            result += "\n"
        return result


@dataclass
class GameHistory:
    phases: List[Phase] = field(default_factory=list)
    
    @property
    def phase_list(self):
        """Compatibility property that returns phase names."""
        return [phase.name for phase in self.phases]

    def add_phase(self, phase_name: str):
        # Avoid adding duplicate phases
        if not self.phases or self.phases[-1].name != phase_name:
            self.phases.append(Phase(name=phase_name))
            logger.debug(f"Added new phase: {phase_name}")
        else:
            logger.warning(f"Phase {phase_name} already exists. Not adding again.")

    def _get_phase(self, phase_name: str) -> Optional[Phase]:
        for phase in reversed(self.phases):
            if phase.name == phase_name:
                return phase
        logger.error(f"Phase {phase_name} not found in history.")
        return None

    def add_plan(self, phase_name: str, power_name: str, plan: str):
        phase = self._get_phase(phase_name)
        if phase:
            phase.plans[power_name] = plan
            logger.debug(f"Added plan for {power_name} in {phase_name}")

    def add_message(
        self, phase_name: str, sender: str, recipient: str, message_content: str
    ):
        phase = self._get_phase(phase_name)
        if phase:
            message = Message(
                sender=sender, recipient=recipient, content=message_content
            )
            phase.messages.append(message)
            logger.debug(f"Added message from {sender} to {recipient} in {phase_name}")

    def add_orders(self, phase_name: str, power_name: str, orders: List[str]):
        phase = self._get_phase(phase_name)
        if phase:
            phase.orders_by_power[power_name].extend(orders)
            logger.debug(f"Added orders for {power_name} in {phase_name}: {orders}")

    def add_results(self, phase_name: str, power_name: str, results: List[List[str]]):
        phase = self._get_phase(phase_name)
        if phase:
            phase.results_by_power[power_name].extend(results)
            logger.debug(f"Added results for {power_name} in {phase_name}: {results}")

    # NEW: Method to add phase summary for a power
    def add_phase_summary(self, phase_name: str, power_name: str, summary: str):
        phase = self._get_phase(phase_name)
        if phase:
            phase.phase_summaries[power_name] = summary
            logger.debug(f"Added phase summary for {power_name} in {phase_name}")

    # NEW: Method to add experience update for a power
    def add_experience_update(self, phase_name: str, power_name: str, update: str):
        phase = self._get_phase(phase_name)
        if phase:
            phase.experience_updates[power_name] = update
            logger.debug(f"Added experience update for {power_name} in {phase_name}")

    def get_strategic_directives(self): 
        # returns for last phase only if exists
        if not self.phases: 
            return {}
        return self.phases[-1].plans
    
    def add_strategic_directive(self, phase_name: str, power_name: str, directive: str):
        """Add a strategic directive (plan) for a power in a specific phase."""
        phase = self._get_phase(phase_name)
        if phase:
            phase.plans[power_name] = directive
            logger.debug(f"Added strategic directive for {power_name} in {phase_name}")
    
    def add_draw_vote(self, phase_name: str, power_name: str, vote: str, reasoning: str = ""):
        """Record a draw vote for a power in a specific phase."""
        phase = self._get_phase(phase_name)
        if phase:
            phase.draw_votes[power_name] = vote
            if reasoning:
                phase.draw_vote_reasoning[power_name] = reasoning
            logger.debug(f"Added draw vote for {power_name} in {phase_name}: {vote}")
    
    def get_draw_vote_history(self, limit: int = 5) -> List[Dict[str, any]]:
        """Get recent draw vote history."""
        history = []
        phases_with_votes = [p for p in self.phases if p.draw_votes]
        
        for phase in phases_with_votes[-limit:]:
            yes_votes = sum(1 for v in phase.draw_votes.values() if v == 'yes')
            no_votes = sum(1 for v in phase.draw_votes.values() if v == 'no')
            neutral_votes = sum(1 for v in phase.draw_votes.values() if v == 'neutral')
            
            history.append({
                'phase': phase.name,
                'summary': f"YES: {yes_votes}, NO: {no_votes}, NEUTRAL: {neutral_votes}",
                'details': phase.draw_votes.copy()
            })
        
        return history
    
    def get_draw_vote_summary(self) -> str:
        """Get a concise summary of recent draw votes."""
        history = self.get_draw_vote_history()
        if not history:
            return "No draw votes recorded yet"
        
        lines = []
        for vote_data in history:
            lines.append(f"{vote_data['phase']}: {vote_data['summary']}")
        
        return "\n".join(lines)

    # NEW METHOD
    def get_messages_this_round(self, power_name: str, current_phase_name: str) -> str:
        current_phase: Optional[Phase] = None
        for phase_obj in self.phases:
            if phase_obj.name == current_phase_name:
                current_phase = phase_obj
                break

        if not current_phase:
            return f"\n(No messages found for current phase: {current_phase_name})\n"

        messages_str = "" 

        global_msgs_content = current_phase.get_global_messages()
        if global_msgs_content:
            messages_str += "**GLOBAL MESSAGES THIS ROUND:**\n"
            messages_str += global_msgs_content
        else:
            messages_str += "**GLOBAL MESSAGES THIS ROUND:**\n (No global messages this round)\n"

        private_msgs_dict = current_phase.get_private_messages(power_name)
        if private_msgs_dict:
            messages_str += "\n**PRIVATE MESSAGES TO/FROM YOU THIS ROUND:**\n"
            for other_power, conversation_content in private_msgs_dict.items():
                messages_str += f" Conversation with {other_power}:\n"
                messages_str += conversation_content
                messages_str += "\n"
        else:
            messages_str += "\n**PRIVATE MESSAGES TO/FROM YOU THIS ROUND:**\n (No private messages this round)\n"
        
        if not global_msgs_content and not private_msgs_dict:
            return f"\n(No messages recorded for current phase: {current_phase_name})\n"

        return messages_str.strip()

    # New method to get recent messages TO a specific power
    def get_recent_messages_to_power(self, power_name: str, limit: int = 3) -> List[Dict[str, str]]:
        """
        Gets the most recent messages sent TO this power, useful for tracking messages that need replies.
        Returns a list of dictionaries with 'sender', 'content', and 'phase' keys.
        """
        if not self.phases:
            return []
            
        # Get the most recent 2 phases including current phase
        recent_phases = self.phases[-2:] if len(self.phases) >= 2 else self.phases[-1:]
        
        # Collect all messages sent TO this power
        messages_to_power = []
        for phase in recent_phases:
            for msg in phase.messages:
                # Personal messages to this power or global messages from others
                if msg.recipient == power_name or (msg.recipient == "GLOBAL" and msg.sender != power_name):
                    # Skip if sender is this power (don't need to respond to own messages)
                    if msg.sender != power_name:
                        messages_to_power.append({
                            'sender': msg.sender,
                            'content': msg.content,
                            'phase': phase.name
                        })
        
        # Add debug logging
        logger.info(f"Found {len(messages_to_power)} messages to {power_name} across {len(recent_phases)} phases")
        if not messages_to_power:
            logger.info(f"No messages found for {power_name} to respond to")
        
        # Take the most recent 'limit' messages
        return messages_to_power[-limit:] if messages_to_power else []
    
    def get_ignored_messages_by_power(self, sender_name: str, num_phases: int = 3) -> Dict[str, List[Dict[str, str]]]:
        """
        Identifies which powers are not responding to messages from sender_name.
        Returns a dict mapping power names to their ignored messages.
        
        A message is considered ignored if:
        1. It was sent from sender_name to another power (private)
        2. No response from that power was received in the same or next phase
        """
        ignored_by_power = {}
        
        # Get recent phases
        recent_phases = self.phases[-num_phases:] if self.phases else []
        if not recent_phases:
            return ignored_by_power
        
        for i, phase in enumerate(recent_phases):
            # Get messages sent by sender to specific powers (not global)
            sender_messages = []
            for msg in phase.messages:
                # Handle both Message objects and dict objects
                if isinstance(msg, Message):
                    if msg.sender == sender_name and msg.recipient not in ['GLOBAL', 'ALL']:
                        sender_messages.append(msg)
                else:  # Assume dict
                    if msg['sender'] == sender_name and msg['recipient'] not in ['GLOBAL', 'ALL']:
                        sender_messages.append(msg)
            
            # Check for responses in this and next phases
            for msg in sender_messages:
                # Handle both Message objects and dict objects
                if isinstance(msg, Message):
                    recipient = msg.recipient
                    msg_content = msg.content
                else:
                    recipient = msg['recipient']
                    msg_content = msg['content']
                
                # Look for responses in current phase and next phases
                found_response = False
                
                # Check remaining phases starting from current
                for check_phase in recent_phases[i:min(i+2, len(recent_phases))]:
                    # Look for messages FROM the recipient TO the sender (direct response)
                    # or FROM the recipient to GLOBAL/ALL that might acknowledge sender
                    response_msgs = []
                    for m in check_phase.messages:
                        if isinstance(m, Message):
                            if m.sender == recipient and (m.recipient == sender_name or 
                               (m.recipient in ['GLOBAL', 'ALL'] and sender_name in m.content)):
                                response_msgs.append(m)
                        else:  # Assume dict
                            if m['sender'] == recipient and (m['recipient'] == sender_name or 
                               (m['recipient'] in ['GLOBAL', 'ALL'] and sender_name in m.get('content', ''))):
                                response_msgs.append(m)
                    
                    if response_msgs:
                        found_response = True
                        break
                
                if not found_response:
                    if recipient not in ignored_by_power:
                        ignored_by_power[recipient] = []
                    ignored_by_power[recipient].append({
                        'phase': phase.name,
                        'content': msg_content
                    })
        
        return ignored_by_power
    
    # MODIFIED METHOD (renamed from get_game_history)
    def get_previous_phases_history(
        self, power_name: str, current_phase_name: str, include_plans: bool = True, num_prev_phases: int = 5
    ) -> str:
        if not self.phases:
            return "\n(No game history available)\n"

        relevant_phases = [p for p in self.phases if p.name != current_phase_name]

        if not relevant_phases:
            return "\n(No previous game history before this round)\n"

        phases_to_report = relevant_phases[-num_prev_phases:]

        if not phases_to_report:
            return "\n(No previous game history available within the lookback window)\n"
        
        game_history_str = ""

        for phase_idx, phase in enumerate(phases_to_report):
            phase_content_str = f"\nPHASE: {phase.name}\n"
            current_phase_has_content = False

            global_msgs = phase.get_global_messages()
            if global_msgs:
                phase_content_str += "\n  GLOBAL MESSAGES:\n"
                phase_content_str += "".join([f"    {line}\n" for line in global_msgs.strip().split('\n')])
                current_phase_has_content = True

            private_msgs = phase.get_private_messages(power_name)
            if private_msgs:
                phase_content_str += "\n  PRIVATE MESSAGES:\n"
                for other_power, messages in private_msgs.items():
                    phase_content_str += f"    Conversation with {other_power}:\n"
                    phase_content_str += "".join([f"      {line}\n" for line in messages.strip().split('\n')])
                current_phase_has_content = True

            if phase.orders_by_power:
                phase_content_str += "\n  ORDERS:\n"
                for power, orders in phase.orders_by_power.items():
                    indicator = " (your power)" if power == power_name else ""
                    phase_content_str += f"    {power}{indicator}:\n"
                    results = phase.results_by_power.get(power, [])
                    for i, order in enumerate(orders):
                        result_str = " (successful)"
                        if i < len(results) and results[i] and not all(r == "" for r in results[i]):
                            result_str = f" ({', '.join(results[i])})"
                        phase_content_str += f"      {order}{result_str}\n"
                    phase_content_str += "\n"
                current_phase_has_content = True
            
            if current_phase_has_content:
                if not game_history_str:
                    game_history_str = "**PREVIOUS GAME HISTORY (Messages, Orders, & Plans from older rounds & phases)**\n"
                game_history_str += phase_content_str
                if phase_idx < len(phases_to_report) -1 :
                    game_history_str += "  " + "-" * 48 + "\n"

        if include_plans and phases_to_report:
            last_reported_previous_phase = phases_to_report[-1]
            if last_reported_previous_phase.plans:
                if not game_history_str:
                    game_history_str = "**PREVIOUS GAME HISTORY (Messages, Orders, & Plans from older rounds & phases)**\n"
                game_history_str += f"\n  PLANS SUBMITTED FOR PHASE {last_reported_previous_phase.name}:\n"
                if power_name in last_reported_previous_phase.plans:
                    game_history_str += f"    Your Plan: {last_reported_previous_phase.plans[power_name]}\n"
                for p_other, plan_other in last_reported_previous_phase.plans.items():
                    if p_other != power_name:
                        game_history_str += f"    {p_other}'s Plan: {plan_other}\n"
                game_history_str += "\n"

        if not game_history_str.replace("**PREVIOUS GAME HISTORY (Messages, Orders, & Plans from older rounds & phases)**\n", "").strip():
            return "\n(No relevant previous game history to display)\n"

        return game_history_str.strip()
