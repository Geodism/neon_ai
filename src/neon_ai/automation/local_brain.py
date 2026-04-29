import sys
import os
import ollama
import json
import re

from neon_ai.bootstrap import legacy_app_root, project_root

for path in (project_root(), legacy_app_root()):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from neon_ai.database.automation import get_project_memory, add_note_to_active_job, send_smart_discovery
from database.read_model import build_chat_context, build_direct_answer, get_debug_summary

class ArgonLocalAI:
    def __init__(self, workflow_json_path="ArgonLeadToCash.json"):
        try:
            current_dir = os.path.dirname(__file__)
            full_path = os.path.join(current_dir, workflow_json_path)
            with open(full_path, 'r') as f:
                self.workflow = json.load(f)
            print("Successfully loaded Argon Lead-to-Cash Workflow rules.")
        except FileNotFoundError:
            print(f"Error: Could not find {workflow_json_path}")
            self.workflow = None

    def chat(self, user_message: str):
        """Allows the desktop UI to have a normal conversation with live read-only database context."""
        result = self.chat_with_context(user_message)
        return result.get("reply") or ""

    def chat_with_context(self, user_message: str):
        """Returns the reply plus resolver/debug metadata for the private chat UI."""
        try:
            live_context = build_chat_context(user_message)
            direct_answer = build_direct_answer(user_message, live_context)
            if direct_answer:
                return {
                    "reply": direct_answer,
                    "intent": live_context.get("intent"),
                    "debug": get_debug_summary(live_context),
                    "used_direct_answer": True,
                }
            response = ollama.chat(model='llama3', messages=[
                {
                    'role': 'system', 
                    'content': (
                        'You are Argon-Prime, the private executive assistant for an electrical contractor. '
                        'You have read-only access to live Argon business data. '
                        'Use the provided live context when it is relevant, but do not claim to perform writes or send messages. '
                        'If the context is incomplete, say what is known and what is uncertain. Be helpful and concise.'
                    )
                },
                {
                    'role': 'system',
                    'content': f"Live Argon database context (read-only):\n{json.dumps(live_context, default=str)}"
                },
                {'role': 'user', 'content': user_message}
            ])
            return {
                "reply": response['message']['content'].strip(),
                "intent": live_context.get("intent"),
                "debug": get_debug_summary(live_context),
                "used_direct_answer": False,
            }
        except Exception as e:
            return {
                "reply": f"Error contacting Local Llama: {e}. Is Ollama running?",
                "intent": None,
                "debug": [],
                "used_direct_answer": False,
            }

    def classify_inbound_email(self, email_body: str, corrected_examples=None):
        if not self.workflow: return "Error: Workflow JSON not loaded."
        step_rules = next((step for step in self.workflow["Steps"] if step["StepID"] == "ClassifyInboundMessage"), None)
        allowed_outputs = step_rules["Output"]["MessageType"]
        examples_text = ""
        if corrected_examples:
            rendered = []
            for example in corrected_examples[:3]:
                rendered.append(
                    "Example:\n"
                    f"Subject: {example.get('subject') or ''}\n"
                    f"Body: {example.get('body') or ''}\n"
                    f"Correct Category: {example.get('corrected_category') or ''}"
                )
            if rendered:
                examples_text = "\nUse these corrected examples as guidance:\n" + "\n\n".join(rendered) + "\n"

        prompt = f"""
        You are the Argon AI Router for an electrical contractor.
        Your job is to read an incoming email and classify it.
        You MUST respond ONLY with one of the following exact categories:
        {allowed_outputs}
        {examples_text}
        Do not include any other text, pleasantries, or explanations. Just the category name.
        Incoming Email:
        "{email_body}"
        """
        try:
            response = ollama.chat(model='llama3', messages=[{'role': 'user', 'content': prompt}])
            return response['message']['content'].strip()
        except Exception as e:
            return "Error"

    def extract_lead_data(self, email_body: str):
        if not self.workflow: return "Error: Workflow JSON not loaded."
        step_rules = next((step for step in self.workflow["Steps"] if step["StepID"] == "CreateOrUpdateLead"), None)
        required_fields = step_rules["RequiredFields"]

        prompt = f"""
        You are the Argon Data Extraction AI. 
        Read the following email and extract these exact fields: {required_fields}
        Respond ONLY with a valid, raw JSON object. Do not include markdown formatting, backticks, or conversational text.
        If a piece of information is completely missing from the email, set its value to null.
        Incoming Email:
        "{email_body}"
        """
        try:
            response = ollama.chat(model='llama3', messages=[{'role': 'user', 'content': prompt}])
            raw_output = response['message']['content'].strip()
            
            match = re.search(r'\{.*\}', raw_output, re.DOTALL)
            if match:
                raw_output = match.group(0)

            return json.loads(raw_output)
        except Exception as e:
            return None

    def extract_customer_profile(self, email_body: str):
        prompt = f"""
        You are the Argon customer intake extraction AI.
        Read the email history below and extract these exact fields if they appear:
        ["CustomerName", "CustomerEmail", "CustomerPhone", "AddressNumber", "StreetName", "CityName", "PostalCode"]
        Respond ONLY with a valid raw JSON object.
        If a value is not clearly present, set it to null.
        Email History:
        "{email_body}"
        """
        try:
            response = ollama.chat(model='llama3', messages=[{'role': 'user', 'content': prompt}])
            raw_output = response['message']['content'].strip()

            match = re.search(r'\{.*\}', raw_output, re.DOTALL)
            if match:
                raw_output = match.group(0)

            return json.loads(raw_output)
        except Exception:
            return None

    def extract_customer_site_profile(self, email_body: str):
        prompt = f"""
        You are the Argon customer verification extraction AI.
        Read the customer reply below and extract these exact fields if they appear:
        [
          "CustomerName",
          "CustomerEmail",
          "CustomerPhone",
          "AddressNumber",
          "StreetName",
          "CityName",
          "PostalCode",
          "SiteSameAsCustomer",
          "SiteName",
          "SiteStreetNumber",
          "SiteStreetName",
          "SiteCity"
        ]
        Respond ONLY with a valid raw JSON object.
        Use "yes" or "no" for "SiteSameAsCustomer" when the customer clearly states it.
        If a value is not clearly present, set it to null.
        Customer Reply:
        "{email_body}"
        """
        try:
            response = ollama.chat(model='llama3', messages=[{'role': 'user', 'content': prompt}])
            raw_output = response['message']['content'].strip()

            match = re.search(r'\{.*\}', raw_output, re.DOTALL)
            if match:
                raw_output = match.group(0)

            return json.loads(raw_output)
        except Exception:
            return None

# --- EXPOSE GLOBAL INSTANCE FOR THE UI TO IMPORT ---
brain_instance = ArgonLocalAI()
