from flask import Flask, render_template, redirect, url_for, request, jsonify, session
import firebase_admin
from firebase_admin import credentials, auth
from firebase_admin import firestore
from google import genai
from google.genai import types
import os
import requests
import PyPDF2
import io
import csv
from werkzeug.utils import secure_filename
from pydantic import BaseModel
from functools import wraps
import json
from dotenv import load_dotenv
load_dotenv()

GEMINI_API_KEY = os.getenv("GENAI_KEY")
APP_SECRET_KEY = os.getenv("SECRET_KEY")
FIREBASE_CONFIG = os.getenv("FIREBASE_CONFIG")
FIREBASE_CONFIG = json.loads(FIREBASE_CONFIG)
CRED = os.getenv("SERVICE_ACC_KEY")

class Card(BaseModel):
    question: str
    answer: str

class FlashcardList(BaseModel):
    cards: list[Card]

app = Flask(__name__)
app.secret_key = APP_SECRET_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

cred = credentials.Certificate('serviceAccountKey.json')
firebase_admin.initialize_app(cred)

db = firestore.client()

def login_required(f):
    def wrapper(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper


@app.route('/api/rephrase', methods=['POST'])
def rephrase_text():
    try:
        data = request.get_json(force=True)
        text = data.get('text', '').strip()
        context = data.get('context', 'general')

        if not text:
            return jsonify({'success': False, 'error': 'No text provided'}), 400

        if context == 'question':
            prompt = (
                "Rephrase this flashcard question to be clearer and more concise "
                "while keeping the exact same meaning.\n\n"
                f"Question: {text}\n\n"
                "Return only the rephrased question."
            )
        elif context == 'answer':
            prompt = (
                "Rephrase this flashcard answer to be clearer and more concise "
                "while keeping the exact same meaning.\n\n"
                f"Answer: {text}\n\n"
                "Return only the rephrased answer."
            )
        else:
            prompt = (
                "Rephrase the following text to be clearer while keeping the same meaning.\n\n"
                f"Text: {text}\n\n"
                "Return only the rephrased text."
            )

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            generation_config={
                "temperature": 0.3,
                "max_output_tokens": 256
            }
        )

        return jsonify({
            'success': True,
            'original': text,
            'rephrased': response.text.strip()
        })

    except Exception as e:
        print("Rephrase error:", e)
        return jsonify({'success': False, 'error': 'Rephrasing failed'}), 500

###

@app.route('/ai-generate')
@login_required
def ai_generate():
    return render_template('ai_generate.html')

@app.route('/api/generate-flashcards', methods=['POST'])
@login_required
def generate_flashcards():
    try:
        # 1. Extract form inputs
        title = request.form.get('title', 'Untitled Stack').strip()
        description = request.form.get('description', '').strip()
        num_cards = int(request.form.get('num_cards', 10))
        text_input = request.form.get('text_input', '').strip()

        # 2. Build the multimodal prompt
        # We start with a list that Gemini expects: [text, part, text, ...]
        prompt_parts = [
            f"Generate exactly {num_cards} academic flashcards based on the provided content."
        ]

        if text_input:
            prompt_parts.append(f"Additional Text Context: {text_input}")

        if 'pdf_file' in request.files:
            pdf_file = request.files['pdf_file']
            if pdf_file and pdf_file.filename:
                # IMPORTANT: Use types.Part.from_bytes to fix your validation error
                pdf_data = pdf_file.read()
                pdf_part = types.Part.from_bytes(
                    data=pdf_data,
                    mime_type='application/pdf'
                )
                prompt_parts.append(pdf_part)

        # 3. Call Gemini with Structured Output
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt_parts,
            config=types.GenerateContentConfig(
                response_mime_type='application/json',
                response_schema=FlashcardList, # Forces AI to follow our Pydantic model
            )
        )

        # 4. Parse the AI response
        # response.parsed contains an instance of FlashcardList
        flashcards_obj = response.parsed
        if not flashcards_obj or not flashcards_obj.cards:
            return jsonify({'success': False, 'error': 'AI failed to generate cards'}), 500

        # Convert Pydantic objects to plain dictionaries for Firestore
        cards_data = [card.model_dump() for card in flashcards_obj.cards]

        # 5. Save to Firestore
        # This matches your specific database structure
        stack_data = {
            'title': title,
            'description': description,
            'visibility': 'private',
            'owner_uid': session['user']['uid'],
            'likes': 0,
            'saves': 0,
            'created_at': firestore.SERVER_TIMESTAMP,
            'cards': cards_data # This is now our array of {question, answer}
        }

        # Firestore .add returns (time, doc_ref)
        _, doc_ref = db.collection('stacks').add(stack_data)

        return jsonify({
            'success': True, 
            'stack_id': doc_ref.id,
            'count': len(cards_data)
        })

    except Exception as e:
        print(f"Flashcard generation error: {str(e)}")
        return jsonify({'success': False, 'error': 'Generation failed'}), 500
    try:
        title = request.form.get('title', 'Untitled Stack').strip()
        description = request.form.get('description', '').strip()
        num_cards = int(request.form.get('num_cards', 10))
        text_input = request.form.get('text_input', '').strip()

        # Prepare contents list for Gemini (handles multi-modal input)
        prompt_content = [f"Generate exactly {num_cards} academic flashcards from the provided materials."]
        
        # Add Text if provided
        if text_input:
            prompt_content.append(f"Text Content: {text_input}")
            
        # Add PDF bytes directly if provided (Gemini 2.0 can read PDFs natively)
        if 'pdf_file' in request.files:
            pdf_file = request.files['pdf_file']
            if pdf_file.filename:
                pdf_bytes = pdf_file.read()
                prompt_content.append({'mime_type': 'application/pdf', 'data': pdf_bytes})

        # Generate using Structured Output
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt_content,
            config={
                'response_mime_type': 'application/json',
                'response_schema': CardStack,
            }
        )

        # Parse and convert to list of dictionaries for Firestore
        generated_cards = [card.model_dump() for card in response.parsed.cards]

        # Save to Firestore using your exact structure
        new_stack = {
            'title': title,
            'description': description,
            'visibility': 'private',
            'owner_uid': session['user']['uid'],
            'likes': 0,
            'saves': 0,
            'created_at': firestore.SERVER_TIMESTAMP,
            'cards': generated_cards  # Array of {question, answer}
        }

        doc_ref = db.collection('stacks').add(new_stack)

        return jsonify({'success': True, 'stack_id': doc_ref[1].id})

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({'success': False, 'error': "Failed to generate cards"}), 500

@app.route('/')
def index():
    if 'user' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login')
def login():
    return render_template('login.html', config=FIREBASE_CONFIG)

@app.route('/signup')
def signup():
    return render_template('signup.html', config=FIREBASE_CONFIG)

@app.route('/api/verify-token', methods=['POST'])
def verify_token():
    token = request.json.get('token')
    try:
        decoded_token = auth.verify_id_token(token)
        user_info = {
            'uid': decoded_token['uid'],
            'email': decoded_token.get('email', '')
        }
        session['user'] = user_info
        return jsonify({'success': True, 'user': user_info})
    except Exception as e:
        print(f"Token verification error: {str(e)}")  # Add this line
        return jsonify({'success': False, 'error': str(e)}), 401

@app.route('/logout')
def logout():
    session.pop('user', None)
    return render_template('logout.html', config=FIREBASE_CONFIG)

#### AUTH ENDS HERE.

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', user=session['user'])

@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html', user=session['user'])

@app.route('/flashcards/<stack_id>')
@login_required
def flashcards(stack_id):
    stack = db.collection('stacks').document(stack_id).get()
    
    if not stack.exists:
        return "Stack not found", 404
    
    data = stack.to_dict()
    
    # Convert cards back to arrays for the template
    flashcards = [[card['question'], card['answer']] for card in data['cards']]
    
    return render_template('flashcards.html', flashcards=flashcards)

@app.route('/create-stack')
@login_required
def create_stack():
    return render_template('stack_create.html')

@app.route('/api/create-stack', methods=['POST'])
@login_required
def api_create_stack():
    try:
        data = request.json
        
        # Convert cards from arrays to objects
        cards = []
        for card in data['cards']:
            cards.append({
                'question': card[0],
                'answer': card[1]
            })
        
        # Create stack in Firestore
        db.collection('stacks').add({
            'title': data['title'],
            'description': data['description'],
            'visibility': data['visibility'],
            'owner_uid': session['user']['uid'],
            'likes': 0,
            'saves': 0,
            'created_at': firestore.SERVER_TIMESTAMP,
            'cards': cards  # Now it's a list of objects, not nested arrays
        })
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/my-stacks')
@login_required
def my_stacks():
    user_id = session['user']['uid']
    stacks_ref = db.collection('stacks').where('owner_uid', '==', user_id).order_by('created_at', direction=firestore.Query.DESCENDING).get()
    
    stacks = []
    for stack in stacks_ref:
        stack_data = stack.to_dict()
        stack_data['id'] = stack.id
        
        # Format the date
        if 'created_at' in stack_data and stack_data['created_at']:
            stack_data['created_at'] = stack_data['created_at'].strftime('%Y-%m-%d %H:%M')
        else:
            stack_data['created_at'] = 'N/A'
        
        stacks.append(stack_data)
    
    return render_template('my_stacks.html', stacks=stacks)

@app.route('/edit-stack/<stack_id>')
@login_required
def edit_stack(stack_id):
    stack = db.collection('stacks').document(stack_id).get()
    
    if not stack.exists:
        return "Stack not found", 404
    
    stack_data = stack.to_dict()
    
    # Check if user owns this stack
    if stack_data['owner_uid'] != session['user']['uid']:
        return "Unauthorized", 403
    
    return render_template('stack_edit.html', stack=stack_data, stack_id=stack_id)

@app.route('/api/edit-stack/<stack_id>', methods=['POST'])
@login_required
def api_edit_stack(stack_id):
    try:
        data = request.json
        
        # Check if user owns this stack
        stack = db.collection('stacks').document(stack_id).get()
        if not stack.exists:
            return jsonify({'success': False, 'error': 'Stack not found'}), 404
        
        if stack.to_dict()['owner_uid'] != session['user']['uid']:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        
        # Convert cards from arrays to objects
        cards = []
        for card in data['cards']:
            cards.append({
                'question': card[0],
                'answer': card[1]
            })
        
        # Update stack in Firestore
        db.collection('stacks').document(stack_id).update({
            'title': data['title'],
            'description': data['description'],
            'visibility': data['visibility'],
            'cards': cards
        })
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/delete-stack/<stack_id>', methods=['POST'])
@login_required
def delete_stack(stack_id):
    try:
        stack = db.collection('stacks').document(stack_id).get()
        
        if not stack.exists:
            return "Stack not found", 404
        
        # Check if user owns this stack
        if stack.to_dict()['owner_uid'] != session['user']['uid']:
            return "Unauthorized", 403
        
        # Delete the stack
        db.collection('stacks').document(stack_id).delete()
        
        return redirect(url_for('my_stacks'))
    except Exception as e:
        return f"Error: {str(e)}", 500
    
@app.route('/stack/<stack_id>')
def stack_view(stack_id):
    stack = db.collection('stacks').document(stack_id).get()
    
    if not stack.exists:
        return "Stack not found", 404
    
    stack_data = stack.to_dict()
    
    # Check if stack is private and user doesn't own it
    if stack_data['visibility'] == 'private':
        if 'user' not in session or stack_data['owner_uid'] != session['user']['uid']:
            return "This stack is private", 403
    
    # Format date
    if 'created_at' in stack_data and stack_data['created_at']:
        stack_data['created_at'] = stack_data['created_at'].strftime('%Y-%m-%d %H:%M')
    else:
        stack_data['created_at'] = 'N/A'
    
    # Check if user is logged in
    is_owner = False
    has_saved = False
    has_liked = False
    can_start = False
    
    if 'user' in session:
        user_id = session['user']['uid']
        is_owner = stack_data['owner_uid'] == user_id
        
        # Check if user has saved this stack
        saved_ref = db.collection('user_saves').where('user_id', '==', user_id).where('stack_id', '==', stack_id).get()
        has_saved = len(list(saved_ref)) > 0
        
        # Check if user has liked this stack
        liked_ref = db.collection('user_likes').where('user_id', '==', user_id).where('stack_id', '==', stack_id).get()
        has_liked = len(list(liked_ref)) > 0
        
        # User can start if they own it or have saved it
        can_start = is_owner or has_saved
    
    share_url = request.url_root + 'stack/' + stack_id
    
    return render_template('stack_view.html', 
                          stack=stack_data, 
                          stack_id=stack_id,
                          is_owner=is_owner,
                          has_liked=has_liked,
                          can_start=can_start,
                          share_url=share_url)

@app.route('/api/like-stack/<stack_id>', methods=['POST'])
@login_required
def like_stack(stack_id):
    try:
        user_id = session['user']['uid']
        
        # Check if user already liked this stack
        existing_like = db.collection('user_likes').where('user_id', '==', user_id).where('stack_id', '==', stack_id).get()
        
        if len(list(existing_like)) > 0:
            return jsonify({'success': False, 'error': 'You already liked this stack'}), 400
        
        # Record the like
        db.collection('user_likes').add({
            'user_id': user_id,
            'stack_id': stack_id,
            'liked_at': firestore.SERVER_TIMESTAMP
        })
        
        # Increment likes count
        db.collection('stacks').document(stack_id).update({
            'likes': firestore.Increment(1)
        })
        
        # Get updated count
        stack = db.collection('stacks').document(stack_id).get()
        likes = stack.to_dict()['likes']
        
        return jsonify({'success': True, 'likes': likes})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/unlike-stack/<stack_id>', methods=['POST'])
@login_required
def unlike_stack(stack_id):
    try:
        user_id = session['user']['uid']
        
        # Find and delete the like record
        likes = db.collection('user_likes').where('user_id', '==', user_id).where('stack_id', '==', stack_id).get()
        
        if len(list(likes)) == 0:
            return jsonify({'success': False, 'error': 'You haven\'t liked this stack'}), 400
        
        for like in likes:
            db.collection('user_likes').document(like.id).delete()
        
        # Decrement likes count
        db.collection('stacks').document(stack_id).update({
            'likes': firestore.Increment(-1)
        })
        
        # Get updated count
        stack = db.collection('stacks').document(stack_id).get()
        likes = stack.to_dict()['likes']
        
        return jsonify({'success': True, 'likes': likes})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/save-stack/<stack_id>', methods=['POST'])
@login_required
def save_stack(stack_id):
    try:
        user_id = session['user']['uid']
        
        # Check if already saved
        existing = db.collection('user_saves').where('user_id', '==', user_id).where('stack_id', '==', stack_id).get()
        if len(list(existing)) > 0:
            return jsonify({'success': False, 'error': 'Already saved'}), 400
        
        # Add to user_saves collection
        db.collection('user_saves').add({
            'user_id': user_id,
            'stack_id': stack_id,
            'saved_at': firestore.SERVER_TIMESTAMP
        })
        
        # Increment saves count
        db.collection('stacks').document(stack_id).update({
            'saves': firestore.Increment(1)
        })
        
        # Get updated count
        stack = db.collection('stacks').document(stack_id).get()
        saves = stack.to_dict()['saves']
        
        return jsonify({'success': True, 'saves': saves})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/my-saved-stacks')
@login_required
def my_saved_stacks():
    user_id = session['user']['uid']
    
    # Get all saved stack IDs for this user
    saved_refs = db.collection('user_saves').where('user_id', '==', user_id).get()
    stack_ids = [save.to_dict()['stack_id'] for save in saved_refs]
    
    # Get the actual stacks
    stacks = []
    for stack_id in stack_ids:
        stack = db.collection('stacks').document(stack_id).get()
        if stack.exists:
            stack_data = stack.to_dict()
            stack_data['id'] = stack.id
            stacks.append(stack_data)
    
    return render_template('my_saved_stacks.html', stacks=stacks)

@app.route('/api/unsave-stack/<stack_id>', methods=['POST'])
@login_required
def unsave_stack(stack_id):
    try:
        user_id = session['user']['uid']
        
        # Find and delete the save record
        saved_refs = db.collection('user_saves').where('user_id', '==', user_id).where('stack_id', '==', stack_id).get()
        
        if len(list(saved_refs)) == 0:
            return jsonify({'success': False, 'error': 'Stack not saved'}), 400
        
        for save in saved_refs:
            db.collection('user_saves').document(save.id).delete()
        
        # Decrement saves count
        db.collection('stacks').document(stack_id).update({
            'saves': firestore.Increment(-1)
        })
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/fork-stack/<stack_id>', methods=['POST'])
@login_required
def fork_stack(stack_id):
    try:
        user_id = session['user']['uid']
        
        # Get the original stack
        original_stack = db.collection('stacks').document(stack_id).get()
        
        if not original_stack.exists:
            return jsonify({'success': False, 'error': 'Stack not found'}), 404
        
        original_data = original_stack.to_dict()
        
        # Create a copy with modified attributes
        db.collection('stacks').add({
            'title': f"Copy of {original_data['title']}",
            'description': original_data['description'],
            'visibility': 'private',  # Always private
            'owner_uid': user_id,  # New owner
            'likes': 0,  # Reset likes
            'saves': 0,  # Reset saves
            'created_at': firestore.SERVER_TIMESTAMP,
            'cards': original_data['cards']  # Copy all cards
        })
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/marketplace')
def marketplace():
    # Get all public stacks
    stacks_ref = db.collection('stacks').where('visibility', '==', 'public').get()
    
    stacks = []
    for stack in stacks_ref:
        stack_data = stack.to_dict()
        stack_data['id'] = stack.id
        
        # Format the date for display
        if 'created_at' in stack_data and stack_data['created_at']:
            stack_data['created_at'] = stack_data['created_at'].strftime('%Y-%m-%d %H:%M')
            # Store timestamp for sorting
            stack_data['created_timestamp'] = int(stack_data['created_at'].timestamp()) if hasattr(stack_data['created_at'], 'timestamp') else 0
        else:
            stack_data['created_at'] = 'N/A'
            stack_data['created_timestamp'] = 0
        
        stacks.append(stack_data)
    
    # Sort by date (newest first) by default
    stacks.sort(key=lambda x: x.get('created_timestamp', 0), reverse=True)
    
    return render_template('marketplace.html', stacks=stacks)

@app.route('/api/import-stack', methods=['POST'])
@login_required
def api_import_stack():
    try:
        data = request.json
        
        # Validate required fields
        if 'title' not in data or 'cards' not in data:
            return jsonify({'success': False, 'error': 'Missing required fields (title and cards)'}), 400
        
        # Validate cards format
        if not isinstance(data['cards'], list) or len(data['cards']) == 0:
            return jsonify({'success': False, 'error': 'Cards must be a non-empty array'}), 400
        
        # Convert cards to proper format if needed
        cards = []
        for card in data['cards']:
            if isinstance(card, dict) and 'question' in card and 'answer' in card:
                cards.append({
                    'question': card['question'],
                    'answer': card['answer']
                })
            elif isinstance(card, list) and len(card) == 2:
                cards.append({
                    'question': card[0],
                    'answer': card[1]
                })
            else:
                return jsonify({'success': False, 'error': 'Invalid card format'}), 400
        
        # Create stack in Firestore
        db.collection('stacks').add({
            'title': data.get('title', 'Imported Stack') + " (uploaded)",
            'description': data.get('description', ''),
            'visibility': 'private',  # Always import as private
            'owner_uid': session['user']['uid'],
            'likes': 0,
            'saves': 0,
            'created_at': firestore.SERVER_TIMESTAMP,
            'cards': cards
        })
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)