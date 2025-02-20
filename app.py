import pandas as pd
import uuid
import threading
import re
import os
import requests
import logging
import psycopg2
from fuzzywuzzy import process
from psycopg2 import sql
from typing import Optional, Dict, List
from dotenv import load_dotenv
from datetime import datetime
from pydantic import BaseModel, Field
from langchain.schema import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_chroma import Chroma
from langchain_core.messages import SystemMessage, HumanMessage
from langchain.output_parsers import PydanticOutputParser
from flask import Flask, request, jsonify, session, render_template

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'supersecretkey')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

csv_lock = threading.Lock()

PROGRAMS = {
    'Kids Program': 'Kids Program',
    'Adults Program': 'Adults Program',
    'Ladies-Only Aqua Fitness': 'Ladies-Only Aqua Fitness',
    'Baby & Toddler Program': 'Baby & Toddler Program',
    'Special Needs Program': 'Special Needs Program'
}

uid = 'postgres'
pwd = 'ahmed'
server = "172.27.249.6"
database = "sample"

data_store = []

def get_main_menu():
    return {
        "text": "👋 Welcome to Aquasprint Swimming Academy!\n\nChoose an option:",
        "options": [
            {"value": "Book a Class", "label": "Book a Class"},
            {"value": "Program Information", "label": "Program Information"},
            {"value": "Location & Hours", "label": "Location & Hours"},
            {"value": "Contact Us", "label": "Contact Us"},
            {"value": "Talk to AI Agent", "label": "Talk to AI Agent"}
        ]
    }

# def save_inquiry(data):
#     """ Save inquiry data to PostgreSQL database """
#     conn = None
#     try:
#         conn = psycopg2.connect(
#             dbname=database,
#             user=uid,
#             password=pwd,
#             host=server
#         )
#         cur = conn.cursor()
        
#         query = sql.SQL("""
#             INSERT INTO inquiries (program, name, phone, email, timestamp)
#             VALUES (%s, %s, %s, %s, %s)
#         """)
        
#         cur.execute(query, (
#             data['program'],
#             data['name'],
#             data['phone'],
#             data['email'],
#             data['timestamp']
#         ))
        
#         conn.commit()
#         cur.close()
#     except Exception as e:
#         logger.error(f"Save failed: {str(e)}")
#         raise
#     finally:
#         if conn is not None:
#             conn.close()


@app.route('/add_inquiry', methods=['POST'])
def add_inquiry():
    """ Exposes data as an API endpoint """
    data = request.json  # Assuming the data is sent as JSON
    
    # Store the inquiry in memory
    data_store.append(data)
    return jsonify({"message": "Data saved successfully!"}), 200


@app.route('/get_inquiries', methods=['GET'])
def get_inquiries():
    """ Fetch all stored inquiries via API """
    return jsonify(data_store), 200


# Save inquiry data to PostgreSQL after posting it to the API
def save_inquiry(data):
    """ Post inquiry data to the exposed API and then fetch it to save it to PostgreSQL """
    conn = None
    try:
        # Post the data to the API endpoint (/add_inquiry)
        api_url = 'http://localhost:5000/add_inquiry'
        response = requests.post(api_url, json=data)

        # Check if the data was added successfully
        if response.status_code == 200:
            logger.info(f"Data successfully posted to API.")
        else:
            logger.error(f"Failed to post data to API: {response.status_code}")
            return

        # Fetch data from the API to store it in PostgreSQL
        api_get_url = 'http://localhost:5000/get_inquiries'
        response = requests.get(api_get_url)

        # Check if the API call was successful
        if response.status_code == 200:
            data_list = response.json()  # Get the list of inquiries
        else:
            logger.error(f"Error fetching data from API: {response.status_code}")
            return
        
        # Connect to PostgreSQL
        conn = psycopg2.connect(
            dbname=database,
            user=uid,
            password=pwd,
            host=server
        )
        cur = conn.cursor()

        # Insert each inquiry into PostgreSQL
        for inquiry in data_list:
            query = sql.SQL("""
                INSERT INTO inquiries (program, name, phone, email, timestamp)
                VALUES (%s, %s, %s, %s, %s)
            """)

            cur.execute(query, (
                inquiry['program'],
                inquiry['name'],
                inquiry['phone'],
                inquiry['email'],
                inquiry['timestamp']
            ))

        # Commit and close connection
        conn.commit()
        cur.close()
        logger.info(f"Data successfully saved to PostgreSQL.")
    except Exception as e:
        logger.error(f"Save failed: {str(e)}")
        raise
    finally:
        if conn is not None:
            conn.close()


class BookingInfo(BaseModel):
    program: Optional[str] = Field(None, description="The swimming program name")
    name: Optional[str] = Field(None, description="Customer's full name")
    phone: Optional[str] = Field(None, description="Customer's phone number")
    email: Optional[str] = Field(None, description="Customer's email address")

def extract_booking_info(query: str) -> BookingInfo:
    """Extract booking information from a natural language query using LangChain"""

    llm = ChatOpenAI(
        model="deepseek-llm",
        base_url="http://172.27.240.1:11434/v1",
        temperature=0.1
    )
    
    parser = PydanticOutputParser(pydantic_object=BookingInfo)
    
    prompt = f"""
    Extract booking information from the following query. If information is not present, return null for that field.
    
    Query: {query}
    
    Extract these fields:
    - Program name (match to: Kids Program, Adults Program, Ladies-Only Aqua Fitness, Baby & Toddler Program, Special Needs Program), if they are unsure about its name or they have a typo, try being smart and figure out which program from the predefined list they mean.
    - Full name
    - Phone number
    - Email address
    
    {parser.get_format_instructions()}
    """
    
    messages = [
        SystemMessage(content="You are a helpful assistant that extracts booking information from text."),
        HumanMessage(content=prompt)
    ]
    
    response = llm.invoke(messages)
    return parser.parse(response.content)

def get_missing_info(booking_info: BookingInfo) -> list:
    """Identify missing required booking information"""

    missing = []

    if not booking_info.program:
        missing.append('program')
    if not booking_info.name:
        missing.append('name')
    if not booking_info.phone:
        missing.append('phone')
    if not booking_info.email:
        missing.append('email')

    return missing

@app.route('/')
def chat_interface():
    session['session_id'] = str(uuid.uuid4())
    session['state'] = 'MAIN_MENU'
    return render_template('chat.html')

@app.route('/send_message', methods=['POST'])
def handle_message():
    user_input = request.json.get('message', '').strip()
    session_id = session.get('session_id')
    current_state = session.get('state', 'MAIN_MENU')

    response = process_message(user_input, current_state, session_id)

    if response is None:
        return jsonify({
            "text": "⚠️ An error occurred while processing your request. Please try again or type 'menu'"
        })

    if "new_state" in response:
        session['state'] = response.get('new_state', 'MAIN_MENU')

    return jsonify(response)

def process_message(message, current_state, session_id):
    try:
        logger.info(f"Processing message: {message}, Current state: {current_state}, Session ID: {session_id}")

        if message.lower() == 'menu':
            return {
                "text": get_main_menu()['text'],
                "options": get_main_menu()['options'],
                "new_state": 'MAIN_MENU'
            }
            
        if current_state == 'MAIN_MENU':
            return handle_main_menu(message)
        elif current_state == 'PROGRAM_SELECTION':
            return handle_program_selection(message)
        elif current_state == 'PROGRAM_INFO':
            return handle_program_info(message)
        elif current_state == 'BOOKING_PROGRAM':
            return handle_booking(message)
        elif current_state == 'AI_QUERY':
            return handle_ai_query(message)
        else:
            logger.error(f"Unknown state: {current_state}")
            return {"text": "⚠️ Unknown state. Please try again or type 'menu'"}
            
    except Exception as e:
        logger.error(f"Error processing message: {e}")
        return {"text": "⚠️ An error occurred. Please try again or type 'menu'"}

def handle_main_menu(message):
    if message == 'Book a Class':
        return {
            "text": "Choose program:",
            "options": [
                {"value": v, "label": v} for k, v in PROGRAMS.items()
            ],
            "new_state": 'PROGRAM_SELECTION'
        }
    elif message == 'Program Information':
        return {
            "text": "Choose program for details:",
            "options": [
                {"value": v, "label": v} for k, v in PROGRAMS.items()
            ],
            "new_state": 'PROGRAM_INFO'
        }
    elif message == 'Location & Hours':
        return {
            "text": ("🏊‍♂️ Aquasprint Swimming Academy\n\n"
                     "📍 Location: The Sustainable City, Dubai\n"
                     "⏰ Hours: Daily 6AM-10PM\n"
                     "📞 +971542502761\n"
                     "📧 info@aquasprint.ae"),
            "options": [{"value": "menu", "label": "Return to Menu"}]
        }
    elif message == 'Contact Us':
        return {
            "text": ("📞 Contact Us:\n"
                     "Call us at +971542502761\n"
                     "Email: info@aquasprint.ae"),
            "options": [{"value": "menu", "label": "Return to Menu"}]
        }
    elif message == 'Talk to AI Agent':
        return {
            "text": "Ask me anything about our programs!",
            "new_state": 'AI_QUERY'
        }
    elif message in PROGRAMS.values():
        return {
            "text": f"Selected program: {message} What's your full name?",
            "options": [{"value": "menu", "label": "Return to Menu"}]
        }
    else:
        return {
            "text": "Invalid program selection. Please choose a program:",
            "options": [
                {"value": v, "label": v} for k, v in PROGRAMS.items()
            ],
            "new_state": 'PROGRAM_SELECTION'
        }



def handle_program_info(message):
    """ Handles the display of program information based on user choice """
    program = PROGRAMS.get(message)
    if program:
        details = {
            'Kids Program': "👶 Kids Program (4-14 years)\n- 8 skill levels\n- Certified instructors",
            'Adults Program': "🏊 Adults Program\n- Beginner to advanced\n- Flexible scheduling",
            'Ladies-Only Aqua Fitness': "🚺 Ladies-Only Aqua Fitness\n- Women-only sessions\n- Full-body workout",
            'Baby & Toddler Program': "👶👨👩 Baby & Toddler\n- Parent-child classes\n- Water safety basics",
            'Special Needs Program': "🌟 Special Needs Program\n- Adapted curriculum\n- Individual attention"
        }.get(message, "Program details not available")
        
        return {
            "text": f"{program} Details:\n{details}",
            "options": [{"value": "menu", "label": "Return to Menu"}],
            "new_state": "MAIN_MENU"
        }
    else:
        return {
            "text": "Invalid choice. Please select a program:",
            "options": [
                {"value": str(k), "label": v} for k, v in PROGRAMS.items()
            ],
            "new_state": 'PROGRAM_INFO'
        }


def handle_program_selection(message):
    """ Prepares for booking by capturing the selected program """
    # Normalize input: check if it's a valid program name or a number
    program = PROGRAMS.get(message)  # Check if input is a number (key)
    if not program and message in PROGRAMS.values():  
        program = message  # Direct name match

    if program:
        session['booking_data'] = {'program': program}
        session['booking_step'] = 'GET_NAME'
        return {
            "text": f"Selected program: {program}\nWhat's your full name?",
            "new_state": 'BOOKING_PROGRAM'
        }
    
    return {
        "text": "Invalid program selection. Please choose a program:",
        "options": [{"value": v, "label": v} for v in PROGRAMS.values()],
        "new_state": 'PROGRAM_SELECTION'
    }


def handle_booking(message: str) -> dict:
    """Handle individual booking steps"""
    try:
        current_step = session.get('booking_step')
        booking_data = session.get('booking_data', {})
        
        if not current_step:
            return {"text": "⚠️ Booking session expired. Please start over.", "new_state": 'MAIN_MENU'}
        
        field = current_step.split('_')[1].lower()
        booking_data[field] = message
        session['booking_data'] = booking_data
        
        next_missing = get_next_missing_field(booking_data)
        
        if next_missing:
            
            session['booking_step'] = f'GET_{next_missing.upper()}'
            prompts = {
                'program': "Which program would you like to join?",
                'name': "What's your full name?",
                'phone': "📱 What's your phone number?",
                'email': "📧 What's your email address?"
            }
            return {"text": prompts[next_missing]}

        else:

            booking_data['timestamp'] = datetime.now().isoformat()
            save_inquiry(booking_data)
            
            confirmation = (
                "✅ Booking confirmed!\n"
                f"Program: {booking_data['program']}\n"
                f"Name: {booking_data['name']}\n"
                f"Phone: {booking_data['phone']}\n"
                f"Email: {booking_data['email']}\n\n"
                "We'll contact you soon!"
            )
            
            session.pop('booking_data', None)
            session.pop('booking_step', None)
            
            return {
                "text": confirmation,
                "options": [{"value": "menu", "label": "Return to Menu"}],
                "new_state": 'MAIN_MENU'
            }
            
    except Exception as e:
        logger.error(f"Booking error: {str(e)}")
        return {"text": "⚠️ Booking failed. Type 'menu' to restart."}

def extract_email(text: str) -> Optional[str]:
    """Extract email using regex pattern"""
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    matches = re.findall(email_pattern, text)
    return matches[0] if matches else None

def extract_phone(text: str) -> Optional[str]:
    """Extract phone number using regex pattern"""
    phone_pattern = r'(?:\+?\d{1,4}[-.\s]?)?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,4}'
    matches = re.findall(phone_pattern, text)
    return matches[0] if matches else None

def extract_program(text: str) -> Optional[str]:
    """Extract program using fuzzy matching."""
    text_lower = text.lower()
    programs = list(PROGRAMS.values())
    best_match = process.extractOne(text_lower, programs)
    
    if best_match and best_match[1] > 80:
        return best_match[0]
    return None

def extract_name(text: str) -> Optional[str]:
    """Extract name from text with enhanced flexibility for various name expressions."""

    name_patterns = [
        r'(?:my\sname\sis\s)([A-Z][a-zA-Z\'\-]+)',  # "My name is"
        r'(?:i\'?m\s)([A-Z][a-zA-Z\'\-]+)',  # "I'm"
        r'(?:i\sam\s)([A-Z][a-zA-Z\'\-]+)',  # "I am"
        r'(?:this\sis\s)([A-Z][a-zA-Z\'\-]+)',  # "This is"
        r'(?:call\sme\s)([A-Z][a-zA-Z\'\-]+)',  # "Call me"
        r'(?:i\s\'?m\scalled\s)([A-Z][a-zA-Z\'\-]+)',  # "I'm called"
        r'(?:you\scan\scall\sme\s)([A-Z][a-zA-Z\'\-]+)',  # "You can call me"
        r'(?:my\sfriends\scall\sme\s)([A-Z][a-zA-Z\'\-]+)',  # "My friends call me"
        r'(?:it\'s\s)([A-Z][a-zA-Z\'\-]+)',  # "It's"
    ]

    for pattern in name_patterns:
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        if matches:
            return matches[0]
    
    return None

def get_next_missing_field(booking_data: dict) -> Optional[str]:
    """Return the next missing required field"""
    required_fields = ['program', 'name', 'phone', 'email']
    for field in required_fields:
        if not booking_data.get(field):
            return field
    return None

def handle_ai_query(message: str) -> dict:
    """Enhanced AI query handler with booking capabilities"""
    try:

        session.pop('booking_data', None)
        session.pop('booking_step', None)

        booking_keywords = ['book', 'register', 'sign up', 'enroll', 'join']
        is_booking_request = any(keyword in message.lower() for keyword in booking_keywords)
        
        if is_booking_request:

            extracted_data = {
                'email': extract_email(message),
                'phone': extract_phone(message),
                'program': extract_program(message),
                'name': extract_name(message)
            }
            
            booking_data = session.get('booking_data', {})
            
            booking_data.update({k: v for k, v in extracted_data.items() if v is not None})
            
            session['booking_data'] = booking_data
            
            next_missing = get_next_missing_field(booking_data)
            
            if next_missing:

                confirmed_info = []

                if booking_data.get('program'):
                    confirmed_info.append(f"Program: {booking_data['program']}")
                if booking_data.get('name'):
                    confirmed_info.append(f"Name: {booking_data['name']}")
                if booking_data.get('phone'):
                    confirmed_info.append(f"Phone: {booking_data['phone']}")
                if booking_data.get('email'):
                    confirmed_info.append(f"Email: {booking_data['email']}")
                
                info_text = "\n".join(confirmed_info) if confirmed_info else ""
                
                session['booking_step'] = f'GET_{next_missing.upper()}'
                
                prompts = {
                    'program': "Which program would you like to join?",
                    'name': "What's your full name?",
                    'phone': "📱 What's your phone number?",
                    'email': "📧 What's your email address?"
                }
                
                response_text = "Let me help you with the booking.\n\n"
                if info_text:
                    response_text += f"I've got this information:\n{info_text}\n\n"
                response_text += f"Please provide: {prompts[next_missing]}"
                
                if next_missing == 'program':
                    return {
                        "text": response_text,
                        "options": [{"value": k, "label": v} for k, v in PROGRAMS.items()],
                        "new_state": 'BOOKING_PROGRAM'
                    }
                else:
                    return {
                        "text": response_text,
                        "new_state": 'BOOKING_PROGRAM'
                    }
            else:

                booking_data['timestamp'] = datetime.now().isoformat()
                save_inquiry(booking_data)
                
                confirmation = (
                    "✅ Booking confirmed!\n"
                    f"Program: {booking_data['program']}\n"
                    f"Name: {booking_data['name']}\n"
                    f"Phone: {booking_data['phone']}\n"
                    f"Email: {booking_data['email']}\n\n"
                    "We'll contact you soon!"
                )
                
                session.pop('booking_data', None)
                session.pop('booking_step', None)
                
                return {
                    "text": confirmation,
                    "options": [{"value": "menu", "label": "Return to Menu"}],
                    "new_state": 'MAIN_MENU'
                }
            
        embeddings = OpenAIEmbeddings(model="text-embedding-3-large")
        vector_store = Chroma(
            collection_name="example_collection",
            embedding_function=embeddings,
            persist_directory="chroma_db"
        )
        retriever = vector_store.as_retriever(search_kwargs={'k': 100})
        docs = retriever.invoke(message)
        knowledge = "\n\n".join([doc.page_content.strip() for doc in docs])
        
        llm = ChatOpenAI(
            model="deepseek-llm",
            base_url="http://172.27.240.1:11434/v1",
            temperature=0.1
        )
        
        messages = [SystemMessage(content=f"""You're an expert assistant for Aquasprint Swimming Academy. Follow these rules:
            1. Answer ONLY using the knowledge base below
            2. Be concise and professional
            3. If unsure, say "I don't have that information"
            4. Never make up answers
            5. If the user shows interest in booking, remind them they can book directly by saying something like 
               "Would you like to book a class? Just tell me your preferred program and contact details!"

            Knowledge Base:
            {knowledge}"""),
            HumanMessage(content=message)
        ]
        
        ai_response = llm.invoke(messages)
        return {
            "text": f"🤖 AI Agent:\n{ai_response.content}",
            "options": [{"value": "menu", "label": "Return to Menu"}]
        }
    
    except Exception as e:
        logger.error(f"AI Query Failed: {e}")
        return {"text": "Our AI agent is currently busy. Please try again later."}

if __name__ == '__main__':
    app.run(port=5000)