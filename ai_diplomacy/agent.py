import logging
import os
from typing import List, Dict, Optional
import json
import re
import json_repair
import json5  # More forgiving JSON parser

# Assuming BaseModelClient is importable from clients.py in the same directory
from .clients import BaseModelClient, load_model_client 
# Import load_prompt and the new logging wrapper from utils
from .utils import load_prompt, run_llm_and_log, log_llm_response
from .prompt_constructor import build_context_prompt # Added import

logger = logging.getLogger(__name__)

# == Best Practice: Define constants at module level ==
ALL_POWERS = frozenset({"AUSTRIA", "ENGLAND", "FRANCE", "GERMANY", "ITALY", "RUSSIA", "TURKEY"})
ALLOWED_RELATIONSHIPS = ["Enemy", "Unfriendly", "Neutral", "Friendly", "Ally"]

# == New: Helper function to load prompt files reliably ==
def _load_prompt_file(filename: str) -> Optional[str]:
    """Loads a prompt template from the prompts directory."""
    try:
        # Construct path relative to this file's location
        current_dir = os.path.dirname(os.path.abspath(__file__))
        prompts_dir = os.path.join(current_dir, 'prompts')
        filepath = os.path.join(prompts_dir, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        logger.error(f"Prompt file not found: {filepath}")
        return None
    except Exception as e:
        logger.error(f"Error loading prompt file {filepath}: {e}")
        return None

class DiplomacyAgent:
    """
    Represents a stateful AI agent playing as a specific power in Diplomacy.
    It holds the agent's goals, relationships, and private journal,
    and uses a BaseModelClient instance to interact with the LLM.
    """
    def __init__(
        self, 
        power_name: str, 
        client: BaseModelClient, 
        initial_goals: Optional[List[str]] = None,
        initial_relationships: Optional[Dict[str, str]] = None,
    ):
        """
        Initializes the DiplomacyAgent.

        Args:
            power_name: The name of the power this agent represents (e.g., 'FRANCE').
            client: An instance of a BaseModelClient subclass for LLM interaction.
            initial_goals: An optional list of initial strategic goals.
            initial_relationships: An optional dictionary mapping other power names to 
                                     relationship statuses (e.g., 'ALLY', 'ENEMY', 'NEUTRAL').
        """
        if power_name not in ALL_POWERS:
            raise ValueError(f"Invalid power name: {power_name}. Must be one of {ALL_POWERS}")

        self.power_name: str = power_name
        self.client: BaseModelClient = client
        # Initialize goals as empty list, will be populated by initialize_agent_state
        self.goals: List[str] = initial_goals if initial_goals is not None else [] 
        # Initialize relationships to Neutral if not provided
        if initial_relationships is None:
            self.relationships: Dict[str, str] = {p: "Neutral" for p in ALL_POWERS if p != self.power_name}
        else:
            self.relationships: Dict[str, str] = initial_relationships
        self.private_journal: List[str] = []
        self.private_diary: List[str] = [] # New private diary

        # --- Load and set the appropriate system prompt ---
        # Get the directory containing the current file (agent.py)
        current_dir = os.path.dirname(os.path.abspath(__file__))
        # Construct path relative to the current file's directory
        prompts_dir = os.path.join(current_dir, "prompts") 
        power_prompt_filename = os.path.join(prompts_dir, f"{power_name.lower()}_system_prompt.txt")
        default_prompt_filename = os.path.join(prompts_dir, "system_prompt.txt")

        system_prompt_content = load_prompt(power_prompt_filename)

        if not system_prompt_content:
            logger.warning(f"Power-specific prompt '{power_prompt_filename}' not found or empty. Loading default system prompt.")
            # system_prompt_content = load_prompt("system_prompt.txt")
            system_prompt_content = load_prompt(default_prompt_filename)
        else:
             logger.info(f"Loaded power-specific system prompt for {power_name}.")
        # ----------------------------------------------------

        if system_prompt_content: # Ensure we actually have content before setting
             self.client.set_system_prompt(system_prompt_content)
        else:
             logger.error(f"Could not load default system prompt either! Agent {power_name} may not function correctly.")
        logger.info(f"Initialized DiplomacyAgent for {self.power_name} with goals: {self.goals}")
        self.add_journal_entry(f"Agent initialized. Initial Goals: {self.goals}")

    def _extract_json_from_text(self, text: str) -> dict:
        """Extract and parse JSON from text, handling common LLM response formats."""
        if not text or not text.strip():
            logger.warning(f"[{self.power_name}] Empty text provided to JSON extractor")
            return {}
            
        # Store original text for debugging
        original_text = text
        
        # Preprocessing: Normalize common formatting issues
        # This helps with the KeyError: '\n  "negotiation_summary"' problem
        text = re.sub(r'\n\s+"(\w+)"\s*:', r'"\1":', text)  # Remove newlines before keys
        # Fix specific patterns that cause trouble
        problematic_patterns = [
            'negotiation_summary', 'relationship_updates', 'updated_relationships',
            'order_summary', 'goals', 'relationships', 'intent',
            'factors_considered', 'reasoning', 'vote'  # Added draw evaluation fields
        ]
        for pattern in problematic_patterns:
            text = re.sub(fr'\n\s*"{pattern}"', f'"{pattern}"', text)
        
        # Try different patterns to extract JSON
        # Order matters - try most specific patterns first
        patterns = [
            # Special handling for ```{{ ... }}``` format that some models use
            r"```\s*\{\{\s*(.*?)\s*\}\}\s*```",
            # JSON in code blocks with or without language specifier
            r"```(?:json)?\s*\n(.*?)\n\s*```",
            # JSON after "PARSABLE OUTPUT:" or similar
            r"PARSABLE OUTPUT:\s*(\{.*?\})",
            r"JSON:\s*(\{.*?\})",
            # Any JSON object
            r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})",
            # Simple JSON in backticks
            r"`(\{.*?\})`",
        ]
        
        # Try each pattern
        for pattern_idx, pattern in enumerate(patterns):
            matches = re.findall(pattern, text, re.DOTALL)
            if matches:
                for match_idx, match in enumerate(matches):
                    # Multiple attempts with different parsers
                    json_text = match.strip()
                    
                    # Attempt 1: Standard JSON after basic cleaning
                    try:
                        cleaned = self._clean_json_text(json_text)
                        result = json.loads(cleaned)
                        logger.debug(f"[{self.power_name}] Successfully parsed JSON with pattern {pattern_idx}, match {match_idx}")
                        return result
                    except json.JSONDecodeError as e_initial:
                        logger.debug(f"[{self.power_name}] Standard JSON parse failed: {e_initial}")
                        
                        # Attempt 1.5: Try surgical cleaning with original patterns if basic cleaning failed
                        try:
                            # Apply several different cleaning patterns from the old method
                            cleaned_match_candidate = json_text
                            
                            # Pattern 1: Removes 'Sentence.' when followed by ',', '}', or ']'
                            cleaned_match_candidate = re.sub(r'\s*([A-Z][\w\s,]*?\.(?:\s+[A-Z][\w\s,]*?\.)*)\s*(?=[,\}\]])', '', cleaned_match_candidate)
                            
                            # Pattern 2: Removes 'Sentence.' when it's at the very end, before the final '}' of the current scope
                            cleaned_match_candidate = re.sub(r'\s*([A-Z][\w\s,]*?\.(?:\s+[A-Z][\w\s,]*?\.)*)\s*(?=\s*\}\s*$)', '', cleaned_match_candidate)
                            
                            # Pattern 3: Fix for newlines and spaces before JSON keys (common problem with LLMs)
                            cleaned_match_candidate = re.sub(r'\n\s+"(\w+)"\s*:', r'"\1":', cleaned_match_candidate)
                            
                            # Pattern 4: Fix trailing commas in JSON objects
                            cleaned_match_candidate = re.sub(r',\s*}', '}', cleaned_match_candidate)
                            
                            # Pattern 5: Handle specific known problematic patterns
                            for pattern in problematic_patterns:
                                cleaned_match_candidate = cleaned_match_candidate.replace(f'\n  "{pattern}"', f'"{pattern}"')
                            
                            # Pattern 6: Fix quotes - replace single quotes with double quotes for keys
                            cleaned_match_candidate = re.sub(r"'(\w+)'\s*:", r'"\1":', cleaned_match_candidate)

                            # Only try parsing if cleaning actually changed something
                            if cleaned_match_candidate != json_text:
                                logger.debug(f"[{self.power_name}] Surgical cleaning applied. Attempting to parse modified JSON.")
                                return json.loads(cleaned_match_candidate)
                        except json.JSONDecodeError as e_surgical:
                            logger.debug(f"[{self.power_name}] Surgical cleaning didn't work: {e_surgical}")
                    
                    # Attempt 2: json5 (more forgiving)
                    try:
                        result = json5.loads(json_text)
                        logger.debug(f"[{self.power_name}] Successfully parsed with json5")
                        return result
                    except Exception as e:
                        logger.debug(f"[{self.power_name}] json5 parse failed: {e}")
                    
                    # Attempt 3: json-repair
                    try:
                        result = json_repair.loads(json_text)
                        logger.debug(f"[{self.power_name}] Successfully parsed with json-repair")
                        return result
                    except Exception as e:
                        logger.debug(f"[{self.power_name}] json-repair failed: {e}")
        
        # Fallback: Try to find ANY JSON-like structure
        try:
            # Find the first { and last }
            start = text.find('{')
            end = text.rfind('}') + 1  # Include the closing brace
            if start != -1 and end > start:
                potential_json = text[start:end]
                
                # Try all parsers on this extracted text
                for parser_name, parser_func in [
                    ("json", json.loads),
                    ("json5", json5.loads),
                    ("json_repair", json_repair.loads)
                ]:
                    try:
                        cleaned = self._clean_json_text(potential_json) if parser_name == "json" else potential_json
                        result = parser_func(cleaned)
                        logger.debug(f"[{self.power_name}] Fallback parse succeeded with {parser_name}")
                        return result
                    except Exception as e:
                        logger.debug(f"[{self.power_name}] Fallback {parser_name} failed: {e}")
                
                # If standard parsers failed, try aggressive cleaning
                try:
                    # Remove common non-JSON text that LLMs might add
                    cleaned_text = re.sub(r'[^{}[\]"\',:.\d\w\s_-]', '', potential_json)
                    # Replace single quotes with double quotes (common LLM error)
                    text_fixed = re.sub(r"'([^']*)':", r'"\1":', cleaned_text)
                    text_fixed = re.sub(r': *\'([^\']*)\'', r': "\1"', text_fixed)
                    
                    result = json.loads(text_fixed)
                    logger.debug(f"[{self.power_name}] Aggressive cleaning worked")
                    return result
                except json.JSONDecodeError:
                    pass
                    
        except Exception as e:
            logger.debug(f"[{self.power_name}] Fallback extraction failed: {e}")
        
        # Last resort: Try json-repair on the entire text
        try:
            result = json_repair.loads(text)
            logger.warning(f"[{self.power_name}] Last resort json-repair succeeded")
            return result
        except Exception as e:
            logger.error(f"[{self.power_name}] All JSON extraction attempts failed. Original text: {original_text[:500]}...")
            return {}
    
    def _clean_json_text(self, text: str) -> str:
        """Clean common JSON formatting issues from LLM responses."""
        if not text:
            return text
            
        # Remove trailing commas
        text = re.sub(r',\s*}', '}', text)
        text = re.sub(r',\s*]', ']', text)
        
        # Fix newlines before JSON keys
        text = re.sub(r'\n\s+"(\w+)"\s*:', r'"\1":', text)
        
        # Replace single quotes with double quotes for keys
        text = re.sub(r"'(\w+)'\s*:", r'"\1":', text)
        
        # Remove comments (if any)
        text = re.sub(r'//.*$', '', text, flags=re.MULTILINE)
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
        
        # Fix unescaped quotes in values (basic attempt)
        # This is risky but sometimes helps with simple cases
        text = re.sub(r':\s*"([^"]*)"([^",}\]]+)"', r': "\1\2"', text)
        
        # Remove any BOM or zero-width spaces
        text = text.replace('\ufeff', '').replace('\u200b', '')
        
        return text.strip()


    def add_journal_entry(self, entry: str):
        """Adds a formatted entry string to the agent's private journal."""
        # Ensure entry is a string
        if not isinstance(entry, str):
            entry = str(entry)
        self.private_journal.append(entry)
        logger.debug(f"[{self.power_name} Journal]: {entry}")

    def add_diary_entry(self, entry: str, phase: str):
        """Adds a formatted entry string to the agent's private diary."""
        if not isinstance(entry, str):
            entry = str(entry) # Ensure it's a string
        formatted_entry = f"[{phase}] {entry}"
        self.private_diary.append(formatted_entry)
        # Keep diary to a manageable size, e.g., last 100 entries
        #self.private_diary = self.private_diary[-100:] 
        logger.info(f"[{self.power_name}] DIARY ENTRY ADDED for {phase}. Total entries: {len(self.private_diary)}. New entry: {entry[:100]}...")

    def format_private_diary_for_prompt(self, max_entries=40) -> str:
        """Formats the last N private diary entries for inclusion in a prompt."""
        logger.info(f"[{self.power_name}] Formatting diary for prompt. Total entries: {len(self.private_diary)}")
        if not self.private_diary:
            logger.warning(f"[{self.power_name}] No diary entries found when formatting for prompt")
            return "(No diary entries yet)"
        # Get the most recent entries
        recent_entries = self.private_diary[-max_entries:]
        formatted_diary = "\n".join(recent_entries)
        logger.info(f"[{self.power_name}] Formatted {len(recent_entries)} diary entries for prompt. Preview: {formatted_diary[:200]}...")
        return formatted_diary
    
    async def consolidate_year_diary_entries(self, year: str, game: 'Game', log_file_path: str):
        """
        Consolidates all diary entries from a specific year into a concise summary.
        This is called when we're 2+ years past a given year to prevent context bloat.
        
        Args:
            year: The year to consolidate (e.g., "1901")
            game: The game object for context
            log_file_path: Path for logging LLM responses
        """
        logger.info(f"[{self.power_name}] CONSOLIDATION CALLED for year {year}")
        logger.info(f"[{self.power_name}] Current diary has {len(self.private_diary)} total entries")
        
        # Debug: Log first few diary entries to see their format
        if self.private_diary:
            logger.info(f"[{self.power_name}] Sample diary entries:")
            for i, entry in enumerate(self.private_diary[:3]):
                logger.info(f"[{self.power_name}]   Entry {i}: {entry[:100]}...")
        
        # Find all diary entries from the specified year
        year_entries = []
        # Update pattern to match phase format: [S1901M], [F1901M], [W1901A] etc.
        # We need to check for [S1901, [F1901, [W1901
        patterns_to_check = [f"[S{year}", f"[F{year}", f"[W{year}"]
        logger.info(f"[{self.power_name}] Looking for entries matching patterns: {patterns_to_check}")
        
        for i, entry in enumerate(self.private_diary):
            # Check if entry matches any of our patterns
            for pattern in patterns_to_check:
                if pattern in entry:
                    year_entries.append(entry)
                    logger.info(f"[{self.power_name}] Found matching entry {i} with pattern '{pattern}': {entry[:50]}...")
                    break  # Don't add the same entry multiple times
        
        if not year_entries:
            logger.info(f"[{self.power_name}] No diary entries found for year {year} using patterns: {patterns_to_check}")
            return
        
        logger.info(f"[{self.power_name}] Found {len(year_entries)} entries to consolidate for year {year}")
        
        # Load consolidation prompt template
        prompt_template = _load_prompt_file('diary_consolidation_prompt.txt')
        if not prompt_template:
            logger.error(f"[{self.power_name}] Could not load diary_consolidation_prompt.txt")
            return
        
        # Format entries for the prompt
        year_diary_text = "\n\n".join(year_entries)
        
        # Create the consolidation prompt
        prompt = prompt_template.format(
            power_name=self.power_name,
            year=year,
            year_diary_entries=year_diary_text
        )
        
        raw_response = ""
        success_status = "FALSE"
        
        try:
            # Use Gemini 2.5 Flash for consolidation if available
            consolidation_client = load_model_client("openrouter-google/gemini-2.5-flash-preview")
            if not consolidation_client:
                consolidation_client = self.client  # Fallback to agent's own client
                logger.warning(f"[{self.power_name}] Using agent's own model for consolidation instead of Gemini Flash")
            
            # Use the enhanced wrapper with retry logic
            from .utils import run_llm_and_log
            raw_response = await run_llm_and_log(
                client=consolidation_client,
                prompt=prompt,
                log_file_path=log_file_path,
                power_name=self.power_name,
                phase=game.current_short_phase,
                response_type='diary_consolidation',
            )
            
            if raw_response and raw_response.strip():
                consolidated_entry = raw_response.strip()
                
                # Separate entries into consolidated and regular entries
                consolidated_entries = []
                regular_entries = []
                
                for entry in self.private_diary:
                    if entry.startswith("[CONSOLIDATED"):
                        consolidated_entries.append(entry)
                    else:
                        # Check if this is an entry we should remove (from the year being consolidated)
                        should_keep = True
                        for pattern in patterns_to_check:
                            if pattern in entry:
                                should_keep = False
                                break
                        if should_keep:
                            regular_entries.append(entry)
                
                # Create the new consolidated summary
                consolidated_summary = f"[CONSOLIDATED {year}] {consolidated_entry}"
                
                # Sort consolidated entries by year (ascending) to keep historical order
                consolidated_entries.append(consolidated_summary)
                consolidated_entries.sort(key=lambda x: x[14:18], reverse=False)  # Extract year from "[CONSOLIDATED YYYY]"
                
                # Rebuild diary with consolidated entries at the top
                self.private_diary = consolidated_entries + regular_entries
                
                success_status = "TRUE"
                logger.info(f"[{self.power_name}] Successfully consolidated {len(year_entries)} entries from {year} into 1 summary")
                logger.info(f"[{self.power_name}] New diary structure - Total entries: {len(self.private_diary)}, Consolidated: {len(consolidated_entries)}, Regular: {len(regular_entries)}")
                logger.debug(f"[{self.power_name}] Diary order preview:")
                for i, entry in enumerate(self.private_diary[:5]):
                    logger.debug(f"[{self.power_name}]   Entry {i}: {entry[:50]}...")
            else:
                logger.warning(f"[{self.power_name}] Empty response from consolidation LLM")
                success_status = "FALSE: Empty response"
                
        except Exception as e:
            logger.error(f"[{self.power_name}] Error consolidating diary entries: {e}", exc_info=True)
            success_status = f"FALSE: {type(e).__name__}"
        finally:
            if log_file_path:
                log_llm_response(
                    log_file_path=log_file_path,
                    model_name=consolidation_client.model_name if 'consolidation_client' in locals() else self.client.model_name,
                    power_name=self.power_name,
                    phase=game.current_short_phase,
                    response_type='diary_consolidation',
                    raw_input_prompt=prompt,
                    raw_response=raw_response,
                    success=success_status
                )

    async def generate_negotiation_diary_entry(self, game: 'Game', game_history: 'GameHistory', log_file_path: str):
        """
        Generates a diary entry summarizing negotiations and updates relationships.
        This method now includes comprehensive LLM interaction logging.
        """
        logger.info(f"[{self.power_name}] Generating negotiation diary entry for {game.current_short_phase}..." )
        
        full_prompt = ""  # For logging in finally block
        raw_response = "" # For logging in finally block
        success_status = "Failure: Initialized" # Default

        try:
            # Load the template file but safely preprocess it first
            prompt_template_content = _load_prompt_file('negotiation_diary_prompt.txt')
            if not prompt_template_content:
                logger.error(f"[{self.power_name}] Could not load negotiation_diary_prompt.txt. Skipping diary entry.")
                success_status = "Failure: Prompt file not loaded"
                return # Exit early if prompt can't be loaded

            # Prepare context for the prompt
            board_state_dict = game.get_state()
            board_state_str = f"Units: {board_state_dict.get('units', {})}, Centers: {board_state_dict.get('centers', {})}"
            
            messages_this_round = game_history.get_messages_this_round(
                power_name=self.power_name,
                current_phase_name=game.current_short_phase
            )
            if not messages_this_round.strip() or messages_this_round.startswith("\n(No messages"):
                messages_this_round = "(No messages involving your power this round that require deep reflection for diary. Focus on overall situation.)"
            
            current_relationships_str = json.dumps(self.relationships)
            current_goals_str = json.dumps(self.goals)
            formatted_diary = self.format_private_diary_for_prompt()
            
            # Get ignored messages context
            ignored_messages = game_history.get_ignored_messages_by_power(self.power_name)
            ignored_context = ""
            if ignored_messages:
                ignored_context = "\n\nPOWERS NOT RESPONDING TO YOUR MESSAGES:\n"
                for power, msgs in ignored_messages.items():
                    ignored_context += f"{power}:\n"
                    for msg in msgs[-2:]:  # Show last 2 ignored messages per power
                        ignored_context += f"  - Phase {msg['phase']}: {msg['content'][:100]}...\n"
            else:
                ignored_context = "\n\nAll powers have been responsive to your messages."
            
            # Do aggressive preprocessing of the template to fix the problematic patterns
            # This includes removing any newlines or whitespace before JSON keys that cause issues
            for pattern in ['negotiation_summary', 'updated_relationships', 'relationship_updates', 'intent']:
                # Fix the "\n  "key"" pattern that breaks .format()
                prompt_template_content = re.sub(
                    fr'\n\s*"{pattern}"', 
                    f'"{pattern}"', 
                    prompt_template_content
                )
            
            # Escape all curly braces in JSON examples to prevent format() from interpreting them
            # First, temporarily replace the actual template variables
            temp_vars = ['power_name', 'current_phase', 'messages_this_round', 'agent_goals', 
                        'agent_relationships', 'board_state_str', 'ignored_messages_context', 'draw_vote_history']
            for var in temp_vars:
                prompt_template_content = prompt_template_content.replace(f'{{{var}}}', f'<<{var}>>')
            
            # Now escape all remaining braces (which should be JSON)
            prompt_template_content = prompt_template_content.replace('{', '{{')
            prompt_template_content = prompt_template_content.replace('}', '}}')
            
            # Restore the template variables
            for var in temp_vars:
                prompt_template_content = prompt_template_content.replace(f'<<{var}>>', f'{{{var}}}')
            
            # Get draw vote history
            draw_vote_summary = game_history.get_draw_vote_summary()
            
            # Create a dictionary with safe values for formatting
            format_vars = {
                "power_name": self.power_name,
                "current_phase": game.current_short_phase,
                "board_state_str": board_state_str,
                "messages_this_round": messages_this_round,
                "agent_relationships": current_relationships_str,
                "agent_goals": current_goals_str,
                "allowed_relationships_str": ", ".join(ALLOWED_RELATIONSHIPS),
                "private_diary_summary": formatted_diary,
                "ignored_messages_context": ignored_context,
                "draw_vote_history": draw_vote_summary
            }
            
            # Now try to use the template after preprocessing
            try:
                # Apply format with our set of variables
                full_prompt = prompt_template_content.format(**format_vars)
                logger.info(f"[{self.power_name}] Successfully formatted prompt template after preprocessing.")
                success_status = "Using prompt file with preprocessing"                
            except KeyError as e:
                logger.error(f"[{self.power_name}] Error formatting negotiation diary prompt template: {e}. Skipping diary entry.")
                success_status = "Failure: Template formatting error"
                return  # Exit early if prompt formatting fails
            
            logger.debug(f"[{self.power_name}] Negotiation diary prompt:\n{full_prompt[:500]}...")


            logger.debug(f"[{self.power_name}] Negotiation diary prompt:\n{full_prompt[:500]}...")

            raw_response = await run_llm_and_log(
                client=self.client,
                prompt=full_prompt,
                log_file_path=log_file_path, # Pass the main log file path
                power_name=self.power_name,
                phase=game.current_short_phase,
                response_type='negotiation_diary_raw', # For run_llm_and_log context
            )

            logger.debug(f"[{self.power_name}] Raw negotiation diary response: {raw_response[:300]}...")

            parsed_data = None
            try:
                parsed_data = self._extract_json_from_text(raw_response)
                logger.debug(f"[{self.power_name}] Parsed diary data: {parsed_data}")
                success_status = "Success: Parsed diary data"
            except json.JSONDecodeError as e:
                logger.error(f"[{self.power_name}] Failed to parse JSON from diary response: {e}. Response: {raw_response[:300]}...")
                success_status = "Failure: JSONDecodeError"
                # Continue without parsed_data, rely on diary_entry_text if available or just log failure
            
            diary_entry_text = "(LLM diary entry generation or parsing failed.)" # Fallback
            relationships_updated = False

            if parsed_data:
                # Fix 1: Be more robust about extracting the negotiation_summary field
                diary_text_candidate = None
                for key in ['negotiation_summary', 'summary', 'diary_entry']:
                    if key in parsed_data and isinstance(parsed_data[key], str) and parsed_data[key].strip():
                        diary_text_candidate = parsed_data[key].strip()
                        logger.info(f"[{self.power_name}] Successfully extracted '{key}' for diary.")
                        break
                        
                if diary_text_candidate:
                    diary_entry_text = diary_text_candidate
                else:
                    logger.warning(f"[{self.power_name}] Could not find valid summary field in diary response. Using fallback.")
                    # Keep the default fallback text
                
                # Fix 2: Be more robust about extracting relationship updates
                new_relationships = None
                for key in ['relationship_updates', 'updated_relationships', 'relationships']:
                    if key in parsed_data and isinstance(parsed_data[key], dict):
                        new_relationships = parsed_data[key]
                        logger.info(f"[{self.power_name}] Successfully extracted '{key}' for relationship updates.")
                        break
                        
                if isinstance(new_relationships, dict):
                    valid_new_rels = {}
                    for p, r in new_relationships.items():
                        p_upper = str(p).upper()
                        r_title = str(r).title()
                        if p_upper in ALL_POWERS and p_upper != self.power_name and r_title in ALLOWED_RELATIONSHIPS:
                            valid_new_rels[p_upper] = r_title
                        elif p_upper != self.power_name: # Log invalid relationship for a valid power
                            logger.warning(f"[{self.power_name}] Invalid relationship '{r}' for power '{p}' in diary update. Keeping old.")
                    
                    if valid_new_rels:
                        # Log changes before applying
                        for p_changed, new_r_val in valid_new_rels.items():
                            old_r_val = self.relationships.get(p_changed, "Unknown")
                            if old_r_val != new_r_val:
                                logger.info(f"[{self.power_name}] Relationship with {p_changed} changing from {old_r_val} to {new_r_val} based on diary.")
                        self.relationships.update(valid_new_rels)
                        relationships_updated = True
                        success_status = "Success: Applied diary data (relationships updated)"
                    else:
                        logger.info(f"[{self.power_name}] No valid relationship updates found in diary response.")
                        if success_status == "Success: Parsed diary data": # If only parsing was successful before
                             success_status = "Success: Parsed, no valid relationship updates"
                elif new_relationships is not None: # It was provided but not a dict
                    logger.warning(f"[{self.power_name}] 'updated_relationships' from diary LLM was not a dictionary: {type(new_relationships)}")

            # Add the generated (or fallback) diary entry
            self.add_diary_entry(diary_entry_text, game.current_short_phase)
            if relationships_updated:
                self.add_journal_entry(f"[{game.current_short_phase}] Relationships updated after negotiation diary: {self.relationships}")
            
            # If success_status is still the default 'Parsed diary data' but no relationships were updated, refine it.
            if success_status == "Success: Parsed diary data" and not relationships_updated:
                success_status = "Success: Parsed, only diary text applied"

        except Exception as e:
            # Log the full exception details for better debugging
            logger.error(f"[{self.power_name}] Caught unexpected error in generate_negotiation_diary_entry: {type(e).__name__}: {e}", exc_info=True)
            success_status = f"Failure: Exception ({type(e).__name__})"
            # Add a fallback diary entry in case of general error
            self.add_diary_entry(f"(Error generating diary entry: {type(e).__name__})", game.current_short_phase)
        finally:
            if log_file_path: # Ensure log_file_path is provided
                log_llm_response(
                    log_file_path=log_file_path,
                    model_name=self.client.model_name if self.client else "UnknownModel",
                    power_name=self.power_name,
                    phase=game.current_short_phase if game else "UnknownPhase",
                    response_type="negotiation_diary", # Specific type for CSV logging
                    raw_input_prompt=full_prompt,
                    raw_response=raw_response,
                    success=success_status
                )

    async def generate_order_diary_entry(self, game: 'Game', orders: List[str], log_file_path: str):
        """
        Generates a diary entry reflecting on the decided orders.
        """
        logger.info(f"[{self.power_name}] Generating order diary entry for {game.current_short_phase}...")
        
        # Load the template but we'll use it carefully with string interpolation
        prompt_template = _load_prompt_file('order_diary_prompt.txt')
        if not prompt_template:
            logger.error(f"[{self.power_name}] Could not load order_diary_prompt.txt. Skipping diary entry.")
            return

        board_state_dict = game.get_state()
        board_state_str = f"Units: {board_state_dict.get('units', {})}, Centers: {board_state_dict.get('centers', {})}"
        
        orders_list_str = "\n".join([f"- {o}" for o in orders]) if orders else "No orders submitted."
        
        goals_str = "\n".join([f"- {g}" for g in self.goals]) if self.goals else "None"
        relationships_str = "\n".join([f"- {p}: {s}" for p, s in self.relationships.items()]) if self.relationships else "None"

        # Do aggressive preprocessing on the template file
        # Fix any whitespace or formatting issues that could break .format()
        for pattern in ['order_summary']:
            prompt_template = re.sub(fr'\n\s*"{pattern}"', f'"{pattern}"', prompt_template)
        
        # Escape all curly braces in JSON examples to prevent format() from interpreting them
        # First, temporarily replace the actual template variables
        temp_vars = ['power_name', 'current_phase', 'orders_list_str', 'board_state_str', 
                    'agent_goals', 'agent_relationships']
        for var in temp_vars:
            prompt_template = prompt_template.replace(f'{{{var}}}', f'<<{var}>>')
        
        # Now escape all remaining braces (which should be JSON)
        prompt_template = prompt_template.replace('{', '{{')
        prompt_template = prompt_template.replace('}', '}}')
        
        # Restore the template variables
        for var in temp_vars:
            prompt_template = prompt_template.replace(f'<<{var}>>', f'{{{var}}}')
        
        # Create a dictionary of variables for template formatting
        format_vars = {
            "power_name": self.power_name,
            "current_phase": game.current_short_phase,
            "orders_list_str": orders_list_str,
            "board_state_str": board_state_str,
            "agent_goals": goals_str,
            "agent_relationships": relationships_str
        }
        
        # Try to use the template with proper formatting
        try:
            prompt = prompt_template.format(**format_vars)
            logger.info(f"[{self.power_name}] Successfully formatted order diary prompt template.")
        except KeyError as e:
            logger.error(f"[{self.power_name}] Error formatting order diary template: {e}. Skipping diary entry.")
            return  # Exit early if prompt formatting fails
        
        logger.debug(f"[{self.power_name}] Order diary prompt:\n{prompt[:300]}...")


        
        response_data = None
        raw_response = None # Initialize raw_response
        try:
            raw_response = await run_llm_and_log(
                client=self.client,
                prompt=prompt, 
                log_file_path=log_file_path,
                power_name=self.power_name,
                phase=game.current_short_phase,
                response_type='order_diary',
            )

            success_status = "FALSE"
            response_data = None
            actual_diary_text = None # Variable to hold the final diary text

            if raw_response:
                try:
                    response_data = self._extract_json_from_text(raw_response)
                    if response_data:
                        # Directly attempt to get 'order_summary' as per the prompt
                        diary_text_candidate = response_data.get("order_summary")
                        if isinstance(diary_text_candidate, str) and diary_text_candidate.strip():
                            actual_diary_text = diary_text_candidate
                            success_status = "TRUE"
                            logger.info(f"[{self.power_name}] Successfully extracted 'order_summary' for order diary entry.")
                        else:
                            logger.warning(f"[{self.power_name}] 'order_summary' missing, invalid, or empty. Value was: {diary_text_candidate}")
                            success_status = "FALSE" # Explicitly set false if not found or invalid
                    else:
                        # response_data is None (JSON parsing failed)
                        logger.warning(f"[{self.power_name}] Failed to parse JSON from order diary LLM response.")
                        success_status = "FALSE"
                except Exception as e:
                    logger.error(f"[{self.power_name}] Error processing order diary JSON: {e}. Raw response: {raw_response[:200]} ", exc_info=False)
                    success_status = "FALSE"

            log_llm_response(
                log_file_path=log_file_path,
                model_name=self.client.model_name,
                power_name=self.power_name,
                phase=game.current_short_phase,
                response_type='order_diary',
                raw_input_prompt=prompt, # ENSURED
                raw_response=raw_response if raw_response else "",
                success=success_status
            )

            if success_status == "TRUE" and actual_diary_text:
                self.add_diary_entry(actual_diary_text, game.current_short_phase)
                logger.info(f"[{self.power_name}] Order diary entry generated and added.")
            else:
                fallback_diary = f"Submitted orders for {game.current_short_phase}: {', '.join(orders)}. (LLM failed to generate a specific diary entry)"
                self.add_diary_entry(fallback_diary, game.current_short_phase)
                logger.warning(f"[{self.power_name}] Failed to generate specific order diary entry. Added fallback.")

        except Exception as e:
            # Ensure prompt is defined or handled if it might not be (it should be in this flow)
            current_prompt = prompt if 'prompt' in locals() else "[prompt_unavailable_in_exception]"
            current_raw_response = raw_response if 'raw_response' in locals() and raw_response is not None else f"Error: {e}"
            log_llm_response(
                log_file_path=log_file_path,
                model_name=self.client.model_name if hasattr(self, 'client') else "UnknownModel",
                power_name=self.power_name,
                phase=game.current_short_phase if 'game' in locals() and hasattr(game, 'current_short_phase') else "order_phase",
                response_type='order_diary_exception',
                raw_input_prompt=current_prompt, # ENSURED (using current_prompt for safety)
                raw_response=current_raw_response,
                success="FALSE"
            )
            fallback_diary = f"Submitted orders for {game.current_short_phase}: {', '.join(orders)}. (Critical error in diary generation process)"
            self.add_diary_entry(fallback_diary, game.current_short_phase)
            logger.warning(f"[{self.power_name}] Added fallback order diary entry due to critical error.")
        # Rest of the code remains the same

    async def generate_phase_result_diary_entry(
        self, 
        game: 'Game', 
        game_history: 'GameHistory',
        phase_summary: str,
        all_orders: Dict[str, List[str]],
        log_file_path: str
    ):
        """
        Generates a diary entry analyzing the actual phase results,
        comparing them to negotiations and identifying betrayals/collaborations.
        """
        logger.info(f"[{self.power_name}] Generating phase result diary entry for {game.current_short_phase}...")
        
        # Load the template
        prompt_template = _load_prompt_file('phase_result_diary_prompt.txt')
        if not prompt_template:
            logger.error(f"[{self.power_name}] Could not load phase_result_diary_prompt.txt. Skipping diary entry.")
            return
        
        # Format all orders for the prompt
        all_orders_formatted = ""
        for power, orders in all_orders.items():
            orders_str = ", ".join(orders) if orders else "No orders"
            all_orders_formatted += f"{power}: {orders_str}\n"
        
        # Get your own orders
        your_orders = all_orders.get(self.power_name, [])
        your_orders_str = ", ".join(your_orders) if your_orders else "No orders"
        
        # Get recent negotiations for this phase
        messages_this_phase = game_history.get_messages_by_phase(game.current_short_phase)
        your_negotiations = ""
        for msg in messages_this_phase:
            if msg.sender == self.power_name:
                your_negotiations += f"To {msg.recipient}: {msg.content}\n"
            elif msg.recipient == self.power_name:
                your_negotiations += f"From {msg.sender}: {msg.content}\n"
        
        if not your_negotiations:
            your_negotiations = "No negotiations this phase"
        
        # Format relationships
        relationships_str = "\n".join([f"{p}: {r}" for p, r in self.relationships.items()])
        
        # Format goals
        goals_str = "\n".join([f"- {g}" for g in self.goals]) if self.goals else "None"
        
        # Get draw vote history
        draw_vote_summary = game_history.get_draw_vote_summary()
        
        # Create the prompt
        prompt = prompt_template.format(
            power_name=self.power_name,
            current_phase=game.current_short_phase,
            phase_summary=phase_summary,
            all_orders_formatted=all_orders_formatted,
            your_negotiations=your_negotiations,
            pre_phase_relationships=relationships_str,
            agent_goals=goals_str,
            your_actual_orders=your_orders_str,
            draw_vote_history=draw_vote_summary
        )
        
        logger.debug(f"[{self.power_name}] Phase result diary prompt:\n{prompt[:500]}...")
        
        raw_response = ""
        success_status = "FALSE"
        
        try:
            raw_response = await run_llm_and_log(
                client=self.client,
                prompt=prompt,
                log_file_path=log_file_path,
                power_name=self.power_name,
                phase=game.current_short_phase,
                response_type='phase_result_diary',
            )
            
            if raw_response and raw_response.strip():
                # The response should be plain text diary entry
                diary_entry = raw_response.strip()
                self.add_diary_entry(diary_entry, game.current_short_phase)
                success_status = "TRUE"
                logger.info(f"[{self.power_name}] Phase result diary entry generated and added.")
            else:
                fallback_diary = f"Phase {game.current_short_phase} completed. Orders executed as: {your_orders_str}. (Failed to generate detailed analysis)"
                self.add_diary_entry(fallback_diary, game.current_short_phase)
                logger.warning(f"[{self.power_name}] Empty response from LLM. Added fallback phase result diary.")
                success_status = "FALSE"
                
        except Exception as e:
            logger.error(f"[{self.power_name}] Error generating phase result diary: {e}", exc_info=True)
            fallback_diary = f"Phase {game.current_short_phase} completed. Unable to analyze results due to error."
            self.add_diary_entry(fallback_diary, game.current_short_phase)
            success_status = f"FALSE: {type(e).__name__}"
        finally:
            log_llm_response(
                log_file_path=log_file_path,
                model_name=self.client.model_name,
                power_name=self.power_name,
                phase=game.current_short_phase,
                response_type='phase_result_diary',
                raw_input_prompt=prompt,
                raw_response=raw_response,
                success=success_status
            )

    def log_state(self, prefix=""):
        logger.debug(f"[{self.power_name}] {prefix} State: Goals={self.goals}, Relationships={self.relationships}")

    # Make this method async
    async def analyze_phase_and_update_state(self, game: 'Game', board_state: dict, phase_summary: str, game_history: 'GameHistory', log_file_path: str):
        """Analyzes the outcome of the last phase and updates goals/relationships using the LLM."""
        # Use self.power_name internally
        power_name = self.power_name 
        current_phase = game.get_current_phase() # Get phase for logging
        logger.info(f"[{power_name}] Analyzing phase {current_phase} outcome to update state...")
        self.log_state(f"Before State Update ({current_phase})")

        try:
            # 1. Construct the prompt using the dedicated state update prompt file
            prompt_template = _load_prompt_file('state_update_prompt.txt')
            if not prompt_template:
                 logger.error(f"[{power_name}] Could not load state_update_prompt.txt. Skipping state update.")
                 return
 
            # Get previous phase safely from history
            if not game_history or not game_history.phases:
                logger.warning(f"[{power_name}] No game history available to analyze for {game.current_short_phase}. Skipping state update.")
                return

            last_phase = game_history.phases[-1]
            last_phase_name = last_phase.name # Assuming phase object has a 'name' attribute
            
            # Use the provided phase_summary parameter instead of retrieving it
            last_phase_summary = phase_summary
            if not last_phase_summary:
                logger.warning(f"[{power_name}] No summary available for previous phase {last_phase_name}. Skipping state update.")
                return
 
            # == Fix: Use board_state parameter ==
            possible_orders = game.get_all_possible_orders()
            
            # Get formatted diary for context
            formatted_diary = self.format_private_diary_for_prompt()

            context = build_context_prompt(
                game=game,
                board_state=board_state, # Use provided board_state parameter
                power_name=power_name,
                possible_orders=possible_orders, # Pass possible_orders
                game_history=game_history, # Pass game_history
                agent_goals=self.goals,
                agent_relationships=self.relationships,
                agent_private_diary=formatted_diary, # Pass formatted diary
            )

            # Add previous phase summary to the information provided to the LLM
            other_powers = [p for p in game.powers if p != power_name]
            
            # Create a readable board state string from the board_state dict
            board_state_str = f"Board State:\n"
            for p_name, power_data in board_state.get('powers', {}).items():
                # Get units and centers from the board state
                units = power_data.get('units', [])
                centers = power_data.get('centers', [])
                board_state_str += f"  {p_name}: Units={units}, Centers={centers}\n"
            
            # Extract year from the phase name (e.g., "S1901M" -> "1901")
            current_year = last_phase_name[1:5] if len(last_phase_name) >= 5 else "unknown"
            
            prompt = prompt_template.format(
                power_name=power_name,
                current_year=current_year,
                current_phase=last_phase_name, # Analyze the phase that just ended
                board_state_str=board_state_str,
                phase_summary=last_phase_summary, # Use provided phase_summary
                other_powers=str(other_powers), # Pass as string representation
                current_goals="\n".join([f"- {g}" for g in self.goals]) if self.goals else "None",
                current_relationships=str(self.relationships) if self.relationships else "None"
            )
            logger.debug(f"[{power_name}] State update prompt:\n{prompt}")

            # Use the client's raw generation capability - AWAIT the async call USING THE WRAPPER
            
            response = await run_llm_and_log(
                client=self.client,
                prompt=prompt,
                log_file_path=log_file_path,
                power_name=power_name,
                phase=current_phase,
                response_type='state_update',
            )
            logger.debug(f"[{power_name}] Raw LLM response for state update: {response}")

            log_entry_response_type = 'state_update' # Default for log_llm_response
            log_entry_success = "FALSE" # Default
            update_data = None # Initialize

            if response is not None and response.strip(): # Check if response is not None and not just whitespace
                try:
                    update_data = self._extract_json_from_text(response)
                    logger.debug(f"[{power_name}] Successfully parsed JSON: {update_data}")
                    
                    # Ensure update_data is a dictionary
                    if not isinstance(update_data, dict):
                        logger.warning(f"[{power_name}] Extracted data is not a dictionary, type: {type(update_data)}")
                        update_data = {}
                    
                    # Check if essential data ('updated_goals' or 'goals') is present AND is a list (for goals)
                    # For relationships, check for 'updated_relationships' or 'relationships' AND is a dict.
                    # Consider it TRUE if at least one of the primary data structures (goals or relationships) is present and correctly typed.
                    goals_present_and_valid = isinstance(update_data.get('updated_goals'), list) or isinstance(update_data.get('goals'), list)
                    rels_present_and_valid = isinstance(update_data.get('updated_relationships'), dict) or isinstance(update_data.get('relationships'), dict)

                    if update_data and (goals_present_and_valid or rels_present_and_valid):
                        log_entry_success = "TRUE"
                    elif update_data: # Parsed, but maybe not all essential data there or not correctly typed
                        log_entry_success = "PARTIAL" 
                        log_entry_response_type = 'state_update_partial_data'
                    else: # Parsed to None or empty dict/list, or data not in expected format
                        log_entry_success = "FALSE"
                        log_entry_response_type = 'state_update_parsing_empty_or_invalid_data'
                except json.JSONDecodeError as e:
                    logger.error(f"[{power_name}] Failed to parse JSON response for state update: {e}. Raw response: {response}")
                    log_entry_response_type = 'state_update_json_error' 
                    # log_entry_success remains "FALSE"
                except Exception as e:
                    logger.error(f"[{power_name}] Unexpected error parsing state update: {e}")
                    log_entry_response_type = 'state_update_unexpected_error'
                    update_data = {}
                    # log_entry_success remains "FALSE"
            else: # response was None or empty/whitespace
                logger.error(f"[{power_name}] No valid response (None or empty) received from LLM for state update.")
                log_entry_response_type = 'state_update_no_response'
                # log_entry_success remains "FALSE"

            # Log the attempt and its outcome
            log_llm_response(
                log_file_path=log_file_path, 
                model_name=self.client.model_name,
                power_name=power_name,
                phase=current_phase,
                response_type=log_entry_response_type,
                raw_input_prompt=prompt, # ENSURED
                raw_response=response if response is not None else "", # Handle if response is None
                success=log_entry_success
            )

            # Fallback logic if update_data is still None or not usable
            if not update_data or not (isinstance(update_data.get('updated_goals'), list) or isinstance(update_data.get('goals'), list) or isinstance(update_data.get('updated_relationships'), dict) or isinstance(update_data.get('relationships'), dict)):
                 logger.warning(f"[{power_name}] update_data is None or missing essential valid structures after LLM call. Using existing goals and relationships as fallback.")
                 update_data = {
                    "updated_goals": self.goals, 
                    "updated_relationships": self.relationships,
                 }
                 logger.warning(f"[{power_name}] Using existing goals and relationships as fallback: {update_data}")

            # Check for both possible key names (prompt uses "goals"/"relationships", 
            # but code was expecting "updated_goals"/"updated_relationships")
            updated_goals = update_data.get('updated_goals')
            if updated_goals is None:
                updated_goals = update_data.get('goals')
                if updated_goals is not None:
                    logger.debug(f"[{power_name}] Using 'goals' key instead of 'updated_goals'")
            
            updated_relationships = update_data.get('updated_relationships')
            if updated_relationships is None:
                updated_relationships = update_data.get('relationships')
                if updated_relationships is not None:
                    logger.debug(f"[{power_name}] Using 'relationships' key instead of 'updated_relationships'")

            if isinstance(updated_goals, list):
                # Simple overwrite for now, could be more sophisticated (e.g., merging)
                self.goals = updated_goals
                self.add_journal_entry(f"[{game.current_short_phase}] Goals updated based on {last_phase_name}: {self.goals}")
            else:
                logger.warning(f"[{power_name}] LLM did not provide valid 'updated_goals' list in state update.")
                # Keep current goals, no update needed

            if isinstance(updated_relationships, dict):
                # Validate and update relationships
                valid_new_relationships = {}
                invalid_count = 0
                
                for p, r in updated_relationships.items():
                    # Convert power name to uppercase for case-insensitive matching
                    p_upper = p.upper()
                    if p_upper in ALL_POWERS and p_upper != power_name:
                        # Check against allowed labels (case-insensitive)
                        r_title = r.title() if isinstance(r, str) else r  # Convert "enemy" to "Enemy" etc.
                        if r_title in ALLOWED_RELATIONSHIPS:
                            valid_new_relationships[p_upper] = r_title
                        else:
                            invalid_count += 1
                            if invalid_count <= 2:  # Only log first few to reduce noise
                                logger.warning(f"[{power_name}] Received invalid relationship label '{r}' for '{p}'. Ignoring.")
                    else:
                        invalid_count += 1
                        if invalid_count <= 2 and not p_upper.startswith(power_name):  # Only log first few to reduce noise
                            logger.warning(f"[{power_name}] Received relationship for invalid/own power '{p}' (normalized: {p_upper}). Ignoring.")
                
                # Summarize if there were many invalid entries
                if invalid_count > 2:
                    logger.warning(f"[{power_name}] {invalid_count} total invalid relationships were ignored.")
                    
                # Update relationships if the dictionary is not empty after validation
                if valid_new_relationships:
                    self.relationships.update(valid_new_relationships)
                    self.add_journal_entry(f"[{game.current_short_phase}] Relationships updated based on {last_phase_name}: {valid_new_relationships}")
                elif updated_relationships: # Log if the original dict wasn't empty but validation removed everything
                    logger.warning(f"[{power_name}] Found relationships in LLM response but none were valid after normalization. Using defaults.")
                else: # Log if the original dict was empty
                     logger.warning(f"[{power_name}] LLM did not provide valid 'updated_relationships' dict in state update.")
                     # Keep current relationships, no update needed

        except FileNotFoundError:
            logger.error(f"[{power_name}] state_update_prompt.txt not found. Skipping state update.")
        except Exception as e:
            # Catch any other unexpected errors during the update process
            logger.error(f"[{power_name}] Error during state analysis/update for phase {game.current_short_phase}: {e}", exc_info=True)

        self.log_state(f"After State Update ({game.current_short_phase})")

    def update_goals(self, new_goals: List[str]):
        """Updates the agent's strategic goals."""
        self.goals = new_goals
        self.add_journal_entry(f"Goals updated: {self.goals}")
        logger.info(f"[{self.power_name}] Goals updated to: {self.goals}")

    def update_relationship(self, other_power: str, status: str):
        """Updates the agent's perceived relationship with another power."""
        if other_power != self.power_name:
             self.relationships[other_power] = status
             self.add_journal_entry(f"Relationship with {other_power} updated to {status}.")
             logger.info(f"[{self.power_name}] Relationship with {other_power} set to {status}.")
        else:
             logger.warning(f"[{self.power_name}] Attempted to set relationship with self.")

    def get_agent_state_summary(self) -> str:
        """Returns a string summary of the agent's current state."""
        summary = f"Agent State for {self.power_name}:\n"
        summary += f"  Goals: {self.goals}\n"
        summary += f"  Relationships: {self.relationships}\n"
        summary += f"  Journal Entries: {len(self.private_journal)}"
        # Optionally include last few journal entries
        # if self.private_journal:
        #    summary += f"\n  Last Journal Entry: {self.private_journal[-1]}"
        return summary

    def generate_plan(self, game: 'Game', board_state: dict, game_history: 'GameHistory') -> str:
        """Generates a strategic plan using the client and logs it."""
        logger.info(f"Agent {self.power_name} generating strategic plan...")
        try:
            plan = self.client.get_plan(game, board_state, self.power_name, game_history)
            self.add_journal_entry(f"Generated plan for phase {game.current_phase}:\n{plan}")
            logger.info(f"Agent {self.power_name} successfully generated plan.")
            return plan
        except Exception as e:
            logger.error(f"Agent {self.power_name} failed to generate plan: {e}")
            self.add_journal_entry(f"Failed to generate plan for phase {game.current_phase} due to error: {e}")
            return "Error: Failed to generate plan."

    def _analyze_stalemate(self, game_history: 'GameHistory', my_current_centers: int) -> str:
        """
        Analyze whether the game appears to be in a stalemate.
        Returns a string describing the stalemate situation.
        """
        try:
            # Since we don't have historical center data in game_history,
            # we can only provide a basic analysis based on current state
            # and the number of phases that have passed
            
            total_phases = len(game_history.phases)
            if total_phases < 6:
                return "Too early in game to analyze stalemate patterns"
            
            # Extract year from recent phases to see how long the game has been going
            recent_phase_names = [phase.name for phase in game_history.phases[-6:]]
            
            # Basic heuristic: if we're past 1910 and have fewer centers, likely stalemate
            current_phase = game_history.phases[-1].name if game_history.phases else "Unknown"
            
            # Try to extract year from phase name (e.g., "S1905M" -> 1905)
            current_year = 1901  # Default
            try:
                # First check if it looks like a standard phase format
                if len(current_phase) >= 5 and current_phase[1:5].isdigit():
                    current_year = int(current_phase[1:5])
                else:
                    # Look through phase history for a valid year
                    for phase in reversed(game_history.phases):
                        if len(phase.name) >= 5 and phase.name[1:5].isdigit():
                            current_year = int(phase.name[1:5])
                            break
            except Exception as e:
                logger.debug(f"Could not extract year from phases: {e}")
            
            # Stalemate analysis based on game length and current position
            if current_year >= 1910:
                if my_current_centers < 10:
                    return f"Game appears stalemated - Year {current_year} with only {my_current_centers} centers suggests limited expansion opportunities"
                elif my_current_centers < 14:
                    return f"Game shows signs of stalemate - Year {current_year} with {my_current_centers} centers, victory (18) seems distant"
                else:
                    return f"Game is still competitive - Year {current_year} with {my_current_centers} centers, victory is within reach"
            elif current_year >= 1907:
                if my_current_centers < 7:
                    return f"Position appears weak - Year {current_year} with only {my_current_centers} centers"
                else:
                    return f"Game is still developing - Year {current_year}, too early to determine stalemate"
            else:
                return f"Game is in early stages - Year {current_year}, stalemate analysis premature"
            
        except Exception as e:
            logger.warning(f"[{self.power_name}] Error analyzing stalemate: {e}")
            return "Unable to analyze stalemate status"

    async def evaluate_draw_decision(self, game: 'Game', game_history: 'GameHistory', llm_log_file_path: str = None) -> str:
        """
        Evaluates whether to vote for a draw based on the current game state.
        Returns: 'yes', 'no', or 'neutral'
        
        Args:
            game: Current game state
            game_history: Game history object
            llm_log_file_path: Path to the CSV file for logging LLM responses
        """
        logger.info(f"[{self.power_name}] Evaluating draw decision for phase {game.current_short_phase}")
        
        try:
            # Load the draw evaluation prompt
            prompt_template = _load_prompt_file("draw_evaluation_prompt.txt")
            if not prompt_template:
                logger.error(f"[{self.power_name}] Could not load draw evaluation prompt")
                return 'neutral'
            
            # Extract current year from phase (e.g., 'S1901M' -> 1901)
            current_year = 1901  # Default
            try:
                if len(game.current_short_phase) >= 5 and game.current_short_phase[1:5].isdigit():
                    current_year = int(game.current_short_phase[1:5])
                else:
                    # Try to extract from game state or use a fallback
                    logger.warning(f"[{self.power_name}] Unexpected phase format: {game.current_short_phase}")
                    # Try to get year from the phase name in history
                    if game_history.phases:
                        last_phase_name = game_history.phases[-1].name
                        if len(last_phase_name) >= 5 and last_phase_name[1:5].isdigit():
                            current_year = int(last_phase_name[1:5])
            except Exception as e:
                logger.warning(f"[{self.power_name}] Could not extract year from phase: {e}")
            
            # Get power rankings
            power_rankings = []
            for power_name, power in game.powers.items():
                if power.units:  # Only include powers still in the game
                    power_rankings.append(f"{power_name}: {len(power.centers)} supply centers")
            power_rankings.sort(key=lambda x: int(x.split(': ')[1].split()[0]), reverse=True)
            
            # Get my supply centers
            my_power = game.get_power(self.power_name)
            my_supply_centers = len(my_power.centers)
            
            # Get recent history summary from phase names
            recent_phases = []
            phase_count = 0
            for phase in reversed(game_history.phases):
                if phase_count >= 5:  # Last 5 phases
                    break
                # Just show phase names since we don't have historical center data
                recent_phases.append(f"{phase.name}")
                phase_count += 1
            recent_history = "Recent phases: " + ", ".join(reversed(recent_phases)) if recent_phases else "No recent history"
            
            # Get recent conversations (last 3 phases)
            recent_conversations = []
            conversation_phases = 0
            for phase in reversed(game_history.phases):
                if conversation_phases >= 3:
                    break
                if phase.messages:  # Access messages directly from phase object
                    phase_convos = []
                    for msg in phase.messages:
                        if msg.sender == self.power_name or msg.recipient == self.power_name:
                            phase_convos.append(f"  {msg.sender} → {msg.recipient}: {msg.content[:100]}...")
                    if phase_convos:
                        recent_conversations.append(f"{phase.name}:\n" + "\n".join(phase_convos))
                        conversation_phases += 1
            recent_conversations_str = "\n\n".join(reversed(recent_conversations)) if recent_conversations else "No recent diplomatic exchanges"
            
            # Get alliance history - for now just use current relationships
            # since we don't store historical relationship data in game_history
            alliance_history = []
            current_allies = [p for p, r in self.relationships.items() if r in ['Ally', 'Friendly']]
            current_enemies = [p for p, r in self.relationships.items() if r in ['Enemy', 'Unfriendly']]
            if current_allies or current_enemies:
                alliance_history.append(f"Current: Allies={current_allies}, Enemies={current_enemies}")
            alliance_history_str = "\n".join(alliance_history) if alliance_history else "No significant alliance history"
            
            # Get private diary summary (recent entries)
            diary_summary = self.format_private_diary_for_prompt(max_entries=5)
            
            # Analyze for stalemates
            stalemate_info = self._analyze_stalemate(game_history, my_supply_centers)
            
            # Format the prompt
            prompt = prompt_template.format(
                power_name=self.power_name,
                current_year=current_year,
                my_supply_centers=my_supply_centers,
                power_rankings="\n".join(power_rankings),
                recent_history=recent_history if recent_history else "No recent history available",
                recent_conversations=recent_conversations_str,
                alliance_history=alliance_history_str,
                relationships=json.dumps(self.relationships, indent=2),
                goals="\n".join(self.goals) if self.goals else "No specific goals set",
                private_diary_summary=diary_summary,
                stalemate_info=stalemate_info
            )
            
            # Get response from LLM
            response = await run_llm_and_log(
                client=self.client,
                prompt=prompt,
                log_file_path=llm_log_file_path,  # Use the main CSV log file
                power_name=self.power_name,
                phase=game.current_short_phase,
                response_type='draw_evaluation'
            )
            
            # Parse the response
            try:
                # Try to extract JSON from the response
                result = self._extract_json_from_text(response)
                vote = result.get('vote', 'neutral').lower()
                reasoning = result.get('reasoning', 'No reasoning provided')
                factors = result.get('factors_considered', [])
                
                # Validate vote - handle various formats LLMs might use
                vote_lower = vote.lower().strip()
                if vote_lower in ['yes', 'y', 'accept', 'agree', 'draw']:
                    vote = 'yes'
                elif vote_lower in ['no', 'n', 'reject', 'disagree', 'continue']:
                    vote = 'no'
                elif vote_lower in ['neutral', 'undecided', 'abstain', 'maybe']:
                    vote = 'neutral'
                else:
                    logger.warning(f"[{self.power_name}] Invalid vote '{vote}', defaulting to 'neutral'")
                    vote = 'neutral'
                
                # Log the decision
                self.add_journal_entry(f"Draw vote decision: {vote}. Reasoning: {reasoning}")
                self.add_diary_entry(f"Voted '{vote}' on draw proposal. Factors: {', '.join(factors)}", game.current_short_phase)
                logger.info(f"[{self.power_name}] Draw vote: {vote} - {reasoning}")
                
                # Log the structured vote result for analysis
                if llm_log_file_path:
                    vote_summary = {
                        'vote': vote,
                        'reasoning': reasoning,
                        'factors': factors,
                        'my_centers': my_supply_centers,
                        'year': current_year
                    }
                    log_llm_response(
                        log_file_path=llm_log_file_path,
                        model_name=self.client.model_name,
                        power_name=self.power_name,
                        phase=game.current_short_phase,
                        response_type='draw_vote_result',
                        raw_input_prompt=prompt,
                        raw_response=json.dumps(vote_summary),
                        success="TRUE"
                    )
                
                return vote
                
            except Exception as e:
                logger.error(f"[{self.power_name}] Error parsing draw evaluation response: {e}")
                return 'neutral'
                
        except Exception as e:
            logger.error(f"[{self.power_name}] Error in draw evaluation: {e}", exc_info=True)
            return 'neutral'