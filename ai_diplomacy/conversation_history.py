from dotenv import load_dotenv
import logging
from collections import defaultdict

logger = logging.getLogger("utils")
logger.setLevel(logging.INFO)
logging.basicConfig(level=logging.INFO)

load_dotenv()


class ConversationHistory:
    def __init__(self):
        self.phases = []
        self.history_by_power = defaultdict(
            lambda: defaultdict(lambda: defaultdict(str))
        )
        self.global_history = defaultdict(lambda: defaultdict(str))

    def add_message(self, year_phase, power_name, message):
        if year_phase not in self.phases:
            self.phases.append(year_phase)

        if message["recipient"] == "GLOBAL":
            self.global_history["GLOBAL"][year_phase] += (
                f"    {power_name}: {message['content']}\n"
            )
        self.history_by_power[power_name][year_phase][message["recipient"]] += (
            f"    {power_name}: {message['content']}\n"
        )
        self.history_by_power[message["recipient"]][year_phase][power_name] += (
            f"    {power_name}: {message['content']}\n"
        )

    def add_messages(self, year_phase, messages):
        if year_phase in self.data:
            self.data[year_phase]
        else:
            self.phases.append(year_phase)

    def get_conversation_history(self, power_name, num_prev_phases=5):
        phases_to_report = self.phases[-num_prev_phases:]
        conversation_history_str = ""
        if self.global_history["GLOBAL"]:
            conversation_history_str += "GLOBAL:\n"
            for phase in phases_to_report:
                if phase in self.global_history["GLOBAL"]:
                    conversation_history_str += f"\n{phase}:\n\n"
                    conversation_history_str += self.global_history["GLOBAL"][phase]
            conversation_history_str += "\n"
        if self.history_by_power[power_name]:
            conversation_history_str += "PRIVATE:\n"
            for phase in phases_to_report:
                if phase in self.history_by_power[power_name]:
                    conversation_history_str += f"\n{phase}:\n"
                    for power in self.history_by_power[power_name][phase].keys():
                        conversation_history_str += f"\n  {power}:\n\n"
                        conversation_history_str += self.history_by_power[power_name][
                            phase
                        ][power]

        return conversation_history_str
