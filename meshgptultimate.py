#!/usr/bin/env python3
import meshtastic
import meshtastic.tcp_interface
from ollama import chat
import time
import re
import logging
from datetime import datetime

date_time = datetime.now().date()

# This configures logging to save to 'meshgpt.log' 
# It will APPEND to the file so you don't lose old logs.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("meshgpt.log"), # Saves to file
        logging.StreamHandler()              # Still shows in terminal
    ]
)

# Example of how to use it in your code:
logging.info("MeshGPT Active: Shared Channel History + Private DMs")

# 1. Setup Connection
interface = meshtastic.tcp_interface.TCPInterface(hostname="localhost", portNumber=4404)

# 2. Storage for sessions
# We now store by "Conversation ID" (either a sender_id or a channel_number)
user_sessions = {}
SYSTEM_PROMPT = {"role": "system", "content": f"The current date is {date_time}. You are MeshGPT, an AI chatbot running on a Meshtastic mesh network serving the azmsh community in Arizona. Each message will begin with the user's username. Be concise, your replies must be less than 175 characters."}

def on_receive(packet, interface):
    global user_sessions
    try:
        if 'decoded' in packet and packet['decoded']['portnum'] == 'TEXT_MESSAGE_APP':
            
            # --- 1. SETUP IDS & INFO ---
            my_node_num = interface.myInfo.my_node_num
            my_id_str = f"!{hex(my_node_num)[2:]}" 
            target_id = str(packet.get('toId', ""))
            sender_id = str(packet.get('fromId', ""))
            channel_index = packet.get('channel', 0) 
            
            user_msg = packet['decoded']['payload'].decode('utf-8')
            
            # --- 2. GET SENDER NAME (For "Reply" styling) ---
            # Try to find the user's "Long Name" or "Short Name" in the node DB
            sender_name = sender_id # Default to ID if name not found
            if sender_id in interface.nodes:
                node_user = interface.nodes[sender_id].get('user', {})
                # Prefer Short Name (e.g. "Caden") to save bandwidth, fallback to Long Name
                sender_name = node_user.get('shortName', node_user.get('longName', sender_id))

            # --- 3. TRIGGER LOGIC ---
            is_dm = target_id.lower() == my_id_str.lower()
            is_tagged = "@meshgpt" in user_msg.lower()
            
            if not (is_dm or is_tagged):
                return 
            if sender_id.lower() == my_id_str.lower():
                return 

            # --- 4. CONVERSATION ID ---
            conv_id = f"dm_{sender_id}" if is_dm else f"chan_{channel_index}"
            logging.info(f"Generating Reply... {conv_id} by {sender_name} ({sender_id}): {user_msg}")

            # --- 5. PREPARE HISTORY ---
            if conv_id not in user_sessions:
                user_sessions[conv_id] = [SYSTEM_PROMPT]
            
            clean_text = re.sub(r"@meshgpt", "", user_msg, flags=re.IGNORECASE).strip()
            
            # We use the Name in the prompt so the AI knows who it is talking to
            formatted_msg = f"{sender_name}: {clean_text}"
            user_sessions[conv_id].append({"role": "user", "content": formatted_msg})

            # Trim history
            if len(user_sessions[conv_id]) > 9:
                user_sessions[conv_id] = [SYSTEM_PROMPT] + user_sessions[conv_id][-8:]

            # --- 6. GENERATE ---
            start_time = time.perf_counter()
            response = chat(model='llama3.2:1b', messages=user_sessions[conv_id], keep_alive=-1, options={'temperature': 0.2, 'num_predict':50, 'top_p': 0.9, 'repeat_penalty': 1.1, 'num_ctx': 4096, 'top_k': 40})
            gen_time = time.perf_counter() - start_time
            
            ai_reply = response['message']['content']
            # --- 6.5 CLEAN UP THE AI REPLY ---
            # Remove "SenderName:" or "@SenderName" from the start if the AI added it.
            # This Regex looks for the name (case insensitive) followed by optional punctuation.
            pattern = r"^@?" + re.escape(sender_name) + r"[:,\-]?\s*"
            ai_reply = re.sub(pattern, "", ai_reply, flags=re.IGNORECASE).strip()
            
            user_sessions[conv_id].append({"role": "assistant", "content": ai_reply})
            logging.info(f"Replying: {ai_reply} [Generation Time: {gen_time:.1f}s]")
            
            # --- 7. SEND REPLY WITH "TAG" ---
            dest = sender_id if is_dm else "^all"
            
            # IF it is a Public Channel, we prepend "@Name " to mimic a reply
            if not is_dm:
                final_text = f"@{sender_name}, {ai_reply}"
            else:
                # If it's a DM, we don't need to tag them
                final_text = ai_reply

            # Append the stats at the very end
            final_text += f" [{gen_time:.1f}s]"
            
            # Send!
            interface.sendText(final_text, destinationId=dest, channelIndex=channel_index, wantAck=is_dm)
            
    except Exception as e:
        logging.error(f"Error in on_receive: {e}")

meshtastic.pub.subscribe(on_receive, "meshtastic.receive")
print("MeshGPT Active: Shared Channel History + Private DMs")
while True:
    time.sleep(0.1)
