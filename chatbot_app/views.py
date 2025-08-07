from django.shortcuts import render, redirect, get_object_or_404
from .models import ChatSession, ChatMessage, ChatFeedback
import requests
import json
from django.conf import settings
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
import mimetypes
import os
import subprocess
import tempfile
import uuid
import re
from pathlib import Path
from .models import FAQ

def require_login(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.session.get('is_authenticated'):
            return redirect('login')
        return view_func(request, *args, **kwargs)
    return wrapper

# List all chat sessions
@require_login
def session_list_view(request):
    sessions = ChatSession.objects.order_by('-created_at')
    session_previews = []
    for s in sessions:
        first_user_msg = s.messages.filter(sender="user").order_by('timestamp').first()
        preview = first_user_msg.text if first_user_msg else None
        session_previews.append({
            'id': s.id,
            'created_at': s.created_at,
            'preview': preview
        })
    return render(request, "chatbot_app/session_list.html", {"sessions": session_previews})

# Start a new chat session
@require_login
def new_session_view(request):
    session = ChatSession.objects.create()
    return redirect('chat_session', session_id=session.id)

# Chat view for a specific session
@require_login
def chatbot_view(request, session_id=None):
    session = get_object_or_404(ChatSession, id=session_id)
    if request.method == "POST":
        user_input = request.POST.get("user_input")
        bot_response = get_bot_response(user_input)
        # Save user message
        ChatMessage.objects.create(session=session, sender="user", text=user_input)
        # Save bot response
        ChatMessage.objects.create(session=session, sender="bot", text=bot_response)
        return redirect('chat_session', session_id=session.id)
    chat_history = session.messages.order_by('timestamp')
    all_sessions = ChatSession.objects.order_by('-created_at')
    session_previews = []
    for s in all_sessions:
        first_user_msg = s.messages.filter(sender="user").order_by('timestamp').first()
        preview = first_user_msg.text if first_user_msg else None
        session_previews.append({
            'id': s.id,
            'created_at': s.created_at,
            'preview': preview
        })
    return render(request, "chatbot_app/chat.html", {
        "chat_history": chat_history,
        "sessions": all_sessions,
        "current_session": session,
    })

def post_to_dify(query, user_id, conversation_id=None, files=None, response_mode="streaming", inputs=None):
    url = settings.DIFY_API_URL
    headers = {
        "Authorization": f"Bearer {settings.DIFY_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "inputs": inputs or {},
        "query": query,
        "response_mode": response_mode,
        "conversation_id": conversation_id or "",
        "user": user_id,
        "files": files or []
    }
    
    if response_mode == "streaming":
        # For streaming, return the response object directly
        response = requests.post(url, headers=headers, json=payload, stream=True)
        return response
    else:
        # For blocking, return JSON response
        response = requests.post(url, headers=headers, json=payload)
        return response.json()

@csrf_exempt
@require_POST
def dify_proxy(request):
    
    data = json.loads(request.body)
    user_message = data.get("message")
    session_id = data.get("session_id")
    user_id = str(session_id)
    file_id = data.get("file_id")
    files = []
    if file_id:
        files = [{"type": "file", "id": file_id}]

    # Get the session and check if it has a conversation_id
    session = ChatSession.objects.get(id=session_id)
    conversation_id = session.conversation_id

    # Save user message to DB (original message without the appended instruction)
    ChatMessage.objects.create(session=session, sender="user", text=user_message)
    
    # Append the instruction to the message sent to Dify
    enhanced_message = user_message + " Don't simply provide me the steps or sample data or sample code. DO IT FOR ME."
    
    # Update session title with first message if no title exists
    if not session.title:
        # Truncate the message to fit in the title field (255 chars)
        title = user_message[:250] + "..." if len(user_message) > 250 else user_message
        session.title = title
        session.save()

    try:
        # Call Dify API with streaming mode (as required)
        dify_response = post_to_dify(
            query=enhanced_message,
            user_id=user_id,
            conversation_id=conversation_id,
            files=files,
            response_mode="streaming"
        )
        
        # Handle streaming response from Dify
        full_reply = ""
        conversation_id_extracted = None
        
        # Process the streaming response
        print(f"DEBUG: Processing streaming response...")
        for chunk in dify_response.iter_content(chunk_size=1024):
            if chunk:
                chunk_str = chunk.decode('utf-8')
                print(f"DEBUG: Chunk: {chunk_str[:100]}...")
                lines = chunk_str.split('\n')
                
                for line in lines:
                    if line.startswith('data: '):
                        try:
                            data = json.loads(line[6:])
                            print(f"DEBUG: Parsed: {data}")
                            if data.get('event') == 'agent_message':
                                if 'answer' in data:
                                    full_reply += data['answer']
                                    print(f"DEBUG: Added: {data['answer']}")
                            elif data.get('event') == 'message_end':
                                # Extract conversation_id if available
                                if 'conversation_id' in data and not conversation_id:
                                    conversation_id_extracted = data['conversation_id']
                                    print(f"DEBUG: Conversation ID: {conversation_id_extracted}")
                        except json.JSONDecodeError as e:
                            print(f"DEBUG: JSON error: {e}")
                            continue
        
        print(f"DEBUG: Final reply: {full_reply}")
        
        # Save conversation_id if extracted
        if conversation_id_extracted and not conversation_id:
            session.conversation_id = conversation_id_extracted
            session.save()
        
        # Check for Python code blocks and execute them automatically
        python_blocks = extract_python_blocks(full_reply)
        enhanced_reply = full_reply
        
        for i, script_content in enumerate(python_blocks):
            try:
                result = execute_python_script(script_content, session_id)
                if result['success']:
                    # Insert plot display right after the Python code block
                    plot_html = f'\n\n<div class="auto-plot-display">\n<div class="plot-title">Generated Plot {i+1}</div>\n<div class="plot-container">\n<img src="{result["plot_url"]}" alt="Generated Plot" style="max-width: 100%; height: auto; border: 1px solid #e2e8f0; border-radius: 8px;">\n<button class="download-plot-btn" onclick="downloadPlot(\'{result["plot_url"]}\', \'{result["plot_filename"]}\')">📥 Download Plot</button>\n</div>\n</div>\n\n'
                    
                    # Find the exact Python code block and insert plot after it
                    code_block_start = enhanced_reply.find(f'```python\n{script_content}\n```')
                    if code_block_start != -1:
                        # Find the end of this specific code block
                        code_block_end = code_block_start + len(f'```python\n{script_content}\n```')
                        enhanced_reply = enhanced_reply[:code_block_end] + plot_html + enhanced_reply[code_block_end:]
                    else:
                        # Fallback: append at the end
                        enhanced_reply += plot_html
                        
                else:
                    # Insert error message after the Python code block
                    error_html = f'\n\n<div class="plot-error">\n<div class="error">❌ Plot Generation Failed: {result.get("error", "Unknown error")}</div>\n</div>\n\n'
                    
                    code_block_start = enhanced_reply.find(f'```python\n{script_content}\n```')
                    if code_block_start != -1:
                        # Find the end of this specific code block
                        code_block_end = code_block_start + len(f'```python\n{script_content}\n```')
                        enhanced_reply = enhanced_reply[:code_block_end] + error_html + enhanced_reply[code_block_end:]
                    else:
                        # Fallback: append at the end
                        enhanced_reply += error_html
                        
            except Exception as e:
                # Insert error message after the Python code block
                error_html = f'\n\n<div class="plot-error">\n<div class="error">❌ Plot Generation Failed: {str(e)}</div>\n</div>\n\n'
                
                code_block_start = enhanced_reply.find(f'```python\n{script_content}\n```')
                if code_block_start != -1:
                    # Find the end of this specific code block
                    code_block_end = code_block_start + len(f'```python\n{script_content}\n```')
                    enhanced_reply = enhanced_reply[:code_block_end] + error_html + enhanced_reply[code_block_end:]
                else:
                    # Fallback: append at the end
                    enhanced_reply += error_html
        
        # Save bot reply to DB
        if full_reply:
            ChatMessage.objects.create(session=session, sender="bot", text=enhanced_reply)
        else:
            # Fallback if no reply was received
            full_reply = "Sorry, no response received."
            ChatMessage.objects.create(session=session, sender="bot", text=full_reply)
        
        return JsonResponse({
            "reply": enhanced_reply
        })
        
    except Exception as e:
        error_msg = f"Error: {str(e)}"
        ChatMessage.objects.create(session=session, sender="bot", text=error_msg)
        return JsonResponse({"reply": error_msg})


@csrf_exempt
@require_POST
def dify_streaming_proxy(request):
    """Streaming endpoint that streams the raw response first, then processes it"""
    import json
    from django.http import StreamingHttpResponse
    import time
    
    data = json.loads(request.body)
    user_message = data.get("message")
    session_id = data.get("session_id")
    user_id = str(session_id)
    file_id = data.get("file_id")
    files = []
    if file_id:
        files = [{"type": "file", "id": file_id}]

    # Get the session and check if it has a conversation_id
    session = ChatSession.objects.get(id=session_id)
    conversation_id = session.conversation_id

    # Save user message to DB (original message without the appended instruction)
    ChatMessage.objects.create(session=session, sender="user", text=user_message)
    
    # Append the instruction to the message sent to Dify
    enhanced_message = user_message + " Don't simply provide me the steps or sample data or sample code. DO IT FOR ME."
    
    # Update session title with first message if no title exists
    if not session.title:
        # Truncate the message to fit in the title field (255 chars)
        title = user_message[:250] + "..." if len(user_message) > 250 else user_message
        session.title = title
        session.save()

    def stream_response():
        try:
            # Call Dify API with streaming mode
            dify_response = post_to_dify(
                query=enhanced_message,
                user_id=user_id,
                conversation_id=conversation_id,
                files=files,
                response_mode="streaming"
            )
            
            # Handle streaming response from Dify
            full_reply = ""
            conversation_id_extracted = None
            
            # Process the streaming response
            print(f"DEBUG: Processing streaming response...")
            for chunk in dify_response.iter_content(chunk_size=1024):
                if chunk:
                    chunk_str = chunk.decode('utf-8')
                    print(f"DEBUG: Chunk: {chunk_str[:100]}...")
                    lines = chunk_str.split('\n')
                    
                    for line in lines:
                        if line.startswith('data: '):
                            try:
                                data = json.loads(line[6:])
                                print(f"DEBUG: Parsed: {data}")
                                if data.get('event') == 'agent_message':
                                    if 'answer' in data:
                                        answer_chunk = data['answer']
                                        full_reply += answer_chunk
                                        print(f"DEBUG: Added: {answer_chunk}")
                                        # Stream this chunk to frontend
                                        yield f"data: {json.dumps({'type': 'chunk', 'content': answer_chunk})}\n\n"
                                elif data.get('event') == 'message_end':
                                    # Extract conversation_id if available
                                    if 'conversation_id' in data and not conversation_id:
                                        conversation_id_extracted = data['conversation_id']
                                        print(f"DEBUG: Conversation ID: {conversation_id_extracted}")
                            except json.JSONDecodeError as e:
                                print(f"DEBUG: JSON error: {e}")
                                continue
            
            print(f"DEBUG: Final reply: {full_reply}")
            
            # Save conversation_id if extracted
            if conversation_id_extracted and not conversation_id:
                session.conversation_id = conversation_id_extracted
                session.save()
            
            # Signal that streaming is complete
            yield f"data: {json.dumps({'type': 'streaming_complete', 'full_reply': full_reply})}\n\n"
            
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            yield f"data: {json.dumps({'type': 'error', 'error': error_msg})}\n\n"

    return StreamingHttpResponse(
        stream_response(),
        content_type='text/plain'
    )


@csrf_exempt
@require_POST
def process_streamed_response(request):
    """Process the complete streamed response with plots and formatting"""
    import json
    
    data = json.loads(request.body)
    full_reply = data.get("full_reply")
    session_id = data.get("session_id")
    
    if not full_reply or not session_id:
        return JsonResponse({"error": "Missing full_reply or session_id"}, status=400)
    
    try:
        session = ChatSession.objects.get(id=session_id)
        
        # Check for Python code blocks and execute them automatically
        python_blocks = extract_python_blocks(full_reply)
        enhanced_reply = full_reply
        
        for i, script_content in enumerate(python_blocks):
            try:
                result = execute_python_script(script_content, session_id)
                if result['success']:
                    # Insert plot display right after the Python code block
                    plot_html = f'\n\n<div class="auto-plot-display">\n<div class="plot-title">Generated Plot {i+1}</div>\n<div class="plot-container">\n<img src="{result["plot_url"]}" alt="Generated Plot" style="max-width: 100%; height: auto; border: 1px solid #e2e8f0; border-radius: 8px;">\n<button class="download-plot-btn" onclick="downloadPlot(\'{result["plot_url"]}\', \'{result["plot_filename"]}\')">📥 Download Plot</button>\n</div>\n</div>\n\n'
                    
                    # Find the exact Python code block and insert plot after it
                    code_block_start = enhanced_reply.find(f'```python\n{script_content}\n```')
                    if code_block_start != -1:
                        # Find the end of this specific code block
                        code_block_end = code_block_start + len(f'```python\n{script_content}\n```')
                        enhanced_reply = enhanced_reply[:code_block_end] + plot_html + enhanced_reply[code_block_end:]
                    else:
                        # Fallback: append at the end
                        enhanced_reply += plot_html
                        
                else:
                    # Insert error message after the Python code block
                    error_html = f'\n\n<div class="plot-error">\n<div class="error">❌ Plot Generation Failed: {result.get("error", "Unknown error")}</div>\n</div>\n\n'
                    
                    code_block_start = enhanced_reply.find(f'```python\n{script_content}\n```')
                    if code_block_start != -1:
                        # Find the end of this specific code block
                        code_block_end = code_block_start + len(f'```python\n{script_content}\n```')
                        enhanced_reply = enhanced_reply[:code_block_end] + error_html + enhanced_reply[code_block_end:]
                    else:
                        # Fallback: append at the end
                        enhanced_reply += error_html
                        
            except Exception as e:
                # Insert error message after the Python code block
                error_html = f'\n\n<div class="plot-error">\n<div class="error">❌ Plot Generation Failed: {str(e)}</div>\n</div>\n\n'
                
                code_block_start = enhanced_reply.find(f'```python\n{script_content}\n```')
                if code_block_start != -1:
                    # Find the end of this specific code block
                    code_block_end = code_block_start + len(f'```python\n{script_content}\n```')
                    enhanced_reply = enhanced_reply[:code_block_end] + error_html + enhanced_reply[code_block_end:]
                else:
                    # Fallback: append at the end
                    enhanced_reply += error_html
        
        # Save bot reply to DB
        if full_reply:
            ChatMessage.objects.create(session=session, sender="bot", text=enhanced_reply)
        else:
            # Fallback if no reply was received
            full_reply = "Sorry, no response received."
            ChatMessage.objects.create(session=session, sender="bot", text=full_reply)
        
        return JsonResponse({
            "reply": enhanced_reply
        })
        
    except Exception as e:
        error_msg = f"Error: {str(e)}"
        ChatMessage.objects.create(session=session, sender="bot", text=error_msg)
        return JsonResponse({"reply": error_msg})


@csrf_exempt
@require_POST
def feedback_view(request):
    import json
    from .models import ChatFeedback, ChatMessage
    
    data = json.loads(request.body)
    session_id = data.get("session_id")
    message_id = data.get("message_id")
    feedback_type = data.get("feedback_type")  # 'like' or 'dislike'
    
    if not session_id or feedback_type not in ['like', 'dislike']:
        return JsonResponse({"error": "Invalid parameters"}, status=400)
    
    try:
        session = ChatSession.objects.get(id=session_id)
        
        # If message_id is 'new', find the latest bot message
        if message_id == 'new':
            message = ChatMessage.objects.filter(session=session, sender='bot').order_by('-timestamp').first()
        else:
            message = ChatMessage.objects.get(id=message_id, session=session, sender='bot')
        
        if not message:
            return JsonResponse({"error": "No bot message found"}, status=404)
        
        # Check if feedback already exists for this message
        existing_feedback = ChatFeedback.objects.filter(message=message).first()
        
        if existing_feedback:
            if existing_feedback.feedback_type == feedback_type:
                # Same feedback type clicked again - remove it
                existing_feedback.delete()
                return JsonResponse({"success": True, "feedback_type": None, "action": "removed", "message_id": message.id})
            else:
                # Different feedback type - update it
                existing_feedback.feedback_type = feedback_type
                existing_feedback.save()
                return JsonResponse({"success": True, "feedback_type": feedback_type, "action": "updated", "message_id": message.id})
        else:
            # Create new feedback
            ChatFeedback.objects.create(session=session, message=message, feedback_type=feedback_type)
            return JsonResponse({"success": True, "feedback_type": feedback_type, "action": "created", "message_id": message.id})
    except ChatSession.DoesNotExist:
        return JsonResponse({"error": "Session not found"}, status=404)
    except ChatMessage.DoesNotExist:
        return JsonResponse({"error": "Message not found"}, status=404)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_login
def get_feedback_state(request, session_id):
    from .models import ChatFeedback
    
    try:
        session = ChatSession.objects.get(id=session_id)
        feedback_data = {}
        
        # Get all bot messages in this session
        bot_messages = ChatMessage.objects.filter(session=session, sender='bot')
        
        for message in bot_messages:
            feedback = ChatFeedback.objects.filter(message=message).first()
            if feedback:
                feedback_data[message.id] = feedback.feedback_type
        
        return JsonResponse({"feedback_state": feedback_data})
    except ChatSession.DoesNotExist:
        return JsonResponse({"error": "Session not found"}, status=404)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

@csrf_exempt
@require_POST
def execute_python_script_view(request):
    """Execute Python script and return plot data"""
    import json
    
    try:
        data = json.loads(request.body)
        script_content = data.get("script_content")
        session_id = data.get("session_id")
        
        if not script_content or not session_id:
            return JsonResponse({"error": "Missing script_content or session_id"}, status=400)
        
        # Execute the script
        result = execute_python_script(script_content, session_id)
        
        return JsonResponse(result)
        
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)



def delete_session_view(request, session_id):
    from .models import ChatSession
    session = get_object_or_404(ChatSession, id=session_id)
    session.delete()
    # Find the most recent remaining session
    next_session = ChatSession.objects.order_by('-created_at').first()
    if next_session:
        return redirect('chat_session', session_id=next_session.id)
    else:
        return redirect('dashboard')

def extract_python_blocks(text):
    """Extract Python code blocks from text"""
    pattern = r'```python\s*\n([\s\S]*?)\n```'
    matches = re.findall(pattern, text)
    return matches

def execute_python_script(script_content, session_id):
    """Execute Python script and return plot file path if generated"""
    try:
        # Create a unique temporary directory for this execution
        temp_dir = Path(tempfile.gettempdir()) / f"dify_plots_{session_id}_{uuid.uuid4().hex[:8]}"
        temp_dir.mkdir(exist_ok=True)
        
        # Create a temporary Python file
        script_file = temp_dir / "script.py"
        with open(script_file, 'w') as f:
            f.write(script_content)
        
        # Execute the script
        result = subprocess.run(
            ['python', str(script_file)],
            capture_output=True,
            text=True,
            cwd=temp_dir,
            timeout=30  # 30 second timeout
        )
        
        # Look for generated plot files
        plot_files = []
        for ext in ['png', 'jpg', 'jpeg', 'svg', 'pdf']:
            plot_files.extend(temp_dir.glob(f"*.{ext}"))
        
        if plot_files:
            # Return the first plot file found
            plot_file = plot_files[0]
            # Copy to a permanent location
            plots_dir = Path(settings.BASE_DIR) / "static" / "plots"
            plots_dir.mkdir(parents=True, exist_ok=True)
            
            plot_filename = f"plot_{session_id}_{uuid.uuid4().hex[:8]}.{plot_file.suffix}"
            plot_path = plots_dir / plot_filename
            
            import shutil
            shutil.copy2(plot_file, plot_path)
            
            return {
                'success': True,
                'plot_url': f'/static/plots/{plot_filename}',
                'plot_filename': plot_filename,
                'stdout': result.stdout,
                'stderr': result.stderr
            }
        else:
            return {
                'success': False,
                'error': 'No plot file generated',
                'stdout': result.stdout,
                'stderr': result.stderr
            }
            
    except subprocess.TimeoutExpired:
        return {
            'success': False,
            'error': 'Script execution timed out'
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

def get_bot_response(user_input):
    # Placeholder logic
    return f"You said: {user_input}"

def login_view(request):
    error = None
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        if username == "1" and password == "1":
            request.session["is_authenticated"] = True
            return redirect("dashboard")
        else:
            error = "Invalid username or passcode."
    return render(request, "chatbot_app/login.html", {"error": error})

def logout_view(request):
    request.session.flush()
    return redirect('login')

# Dashboard view
@require_login
def dashboard_view(request):
    """Dashboard view"""
    # Get statistics
    total_sessions = ChatSession.objects.count()
    total_feedback = ChatFeedback.objects.count()
    total_likes = ChatFeedback.objects.filter(feedback_type='like').count()
    total_dislikes = ChatFeedback.objects.filter(feedback_type='dislike').count()
    
    # Get sessions with feedback
    sessions_with_feedback_queryset = ChatSession.objects.filter(feedbacks__isnull=False).distinct()
    sessions_with_feedback = []
    
    for session in sessions_with_feedback_queryset:
        likes = ChatFeedback.objects.filter(session=session, feedback_type='like').count()
        dislikes = ChatFeedback.objects.filter(session=session, feedback_type='dislike').count()
        
        sessions_with_feedback.append({
            'session': session,
            'likes': likes,
            'dislikes': dislikes
        })
    
    # Get FAQ sessions
    faq_sessions_queryset = FAQ.objects.all()
    faq_sessions = []
    for faq in faq_sessions_queryset:
        first_user_msg = faq.session.messages.filter(sender="user").order_by('timestamp').first()
        preview = first_user_msg.text if first_user_msg else None
        faq_sessions.append({
            'id': faq.session.id,
            'created_at': faq.session.created_at,
            'preview': preview,
            'faq_created_at': faq.created_at
        })
    
    # Get all sessions for sidebar
    all_sessions = ChatSession.objects.order_by('-created_at')
    session_previews = []
    for s in all_sessions:
        first_user_msg = s.messages.filter(sender="user").order_by('timestamp').first()
        preview = first_user_msg.text if first_user_msg else None
        session_previews.append({
            'id': s.id,
            'created_at': s.created_at,
            'preview': preview
        })
    
    return render(request, "chatbot_app/dashboard.html", {
        "total_sessions": total_sessions,
        "total_feedback": total_feedback,
        "total_likes": total_likes,
        "total_dislikes": total_dislikes,
        "sessions_with_feedback": sessions_with_feedback,
        "faq_sessions": faq_sessions,
        "sessions": all_sessions,
    })

@csrf_exempt
@require_login
def check_faq_status(request, session_id):
    """Check if a session is already in FAQ"""
    try:
        session = ChatSession.objects.get(id=session_id)
        is_in_faq = FAQ.objects.filter(session=session).exists()
        return JsonResponse({
            'success': True,
            'is_in_faq': is_in_faq
        })
    except ChatSession.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Session not found'})

@csrf_exempt
@require_login
def add_to_faq_view(request):
    """Add or remove a chat session from FAQ"""
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            session_id = data.get('session_id')
            message_id = data.get('message_id')
            action = data.get('action', 'add')  # 'add' or 'remove'
            
            if not session_id:
                return JsonResponse({'success': False, 'error': 'Session ID is required'})
            
            # Get the chat session
            try:
                session = ChatSession.objects.get(id=session_id)
            except ChatSession.DoesNotExist:
                return JsonResponse({'success': False, 'error': 'Session not found'})
            
            if action == 'add':
                # Check if FAQ entry already exists for this session
                if FAQ.objects.filter(session=session).exists():
                    return JsonResponse({'success': False, 'error': 'This session is already in FAQ'})
                
                # Create FAQ entry - just store the session
                FAQ.objects.create(session=session)
                
                return JsonResponse({
                    'success': True,
                    'message': 'Added to FAQ successfully',
                    'action': 'added'
                })
            elif action == 'remove':
                # Remove FAQ entry
                faq_entry = FAQ.objects.filter(session=session).first()
                if faq_entry:
                    faq_entry.delete()
                    return JsonResponse({
                        'success': True,
                        'message': 'Removed from FAQ successfully',
                        'action': 'removed'
                    })
                else:
                    return JsonResponse({'success': False, 'error': 'Session not found in FAQ'})
            else:
                return JsonResponse({'success': False, 'error': 'Invalid action'})
            
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Only POST method allowed'})

@require_login
def settings_view(request):
    """Settings page view"""
    from django.templatetags.static import static
    return render(request, "chatbot_app/settings.html", {
        "sessions": ChatSession.objects.order_by('-created_at'),
        "logo_url": static('Lululemon-Emblem-700x394.png'),
    })
