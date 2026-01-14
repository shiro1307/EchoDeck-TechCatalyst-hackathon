from flask import Flask, render_template, redirect, url_for, request, jsonify, session
import firebase_admin
from firebase_admin import credentials, auth
from firebase_admin import firestore
import os

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this'

cred = credentials.Certificate('serviceAccountKey.json')
firebase_admin.initialize_app(cred)

db = firestore.client()

FIREBASE_CONFIG = {
    'apiKey': "AIzaSyAavYnr1JrCmzluDzY0rbaYFRVAj0P0dV8",
    'authDomain': "flash-tech-catalyst.firebaseapp.com",
    'projectId': "flash-tech-catalyst",
    'storageBucket': "flash-tech-catalyst.firebasestorage.app",
    'messagingSenderId': "427078665038",
    'appId': "1:427078665038:web:cb5ccfbd5547c4389f541f"
}

def login_required(f):
    def wrapper(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

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

if __name__ == '__main__':
    app.run(debug=True)