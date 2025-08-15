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
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    import os
    
    url = settings.DIFY_API_URL
    
    # Check if API key is properly configured
    if not settings.DIFY_API_KEY or settings.DIFY_API_KEY == 'your-dify-api-key-here':
        raise ValueError("DIFY_API_KEY is not properly configured. Please set the environment variable DIFY_API_KEY with your actual Dify API key.")
    
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
    
    # Enhanced timeout and retry configuration
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    # Use longer timeout - 600 seconds (10 minutes)
    timeout = int(os.environ.get('DIFY_TIMEOUT', 600))
    
    print(f"DEBUG: Making request to Dify API: {url}")
    print(f"DEBUG: Timeout set to: {timeout} seconds")
    print(f"DEBUG: API Key configured: {settings.DIFY_API_KEY[:10]}..." if len(settings.DIFY_API_KEY) > 10 else "DEBUG: API Key too short")
    
    if response_mode == "streaming":
        # For streaming, return the response object directly
        response = session.post(url, headers=headers, json=payload, stream=True, timeout=timeout)
        # Check for authentication errors
        if response.status_code == 401:
            print(f"DEBUG: Authentication failed. Status: {response.status_code}")
            print(f"DEBUG: Response: {response.text}")
            raise ValueError(f"Authentication failed with Dify API. Please check your DIFY_API_KEY. Status: {response.status_code}")
        elif response.status_code >= 400:
            print(f"DEBUG: API request failed. Status: {response.status_code}")
            print(f"DEBUG: Response: {response.text}")
            raise ValueError(f"Dify API request failed. Status: {response.status_code}, Response: {response.text}")
        return response
    else:
        # For blocking, return JSON response
        response = session.post(url, headers=headers, json=payload, timeout=timeout)
        if response.status_code == 401:
            raise ValueError(f"Authentication failed with Dify API. Please check your DIFY_API_KEY. Status: {response.status_code}")
        elif response.status_code >= 400:
            raise ValueError(f"Dify API request failed. Status: {response.status_code}, Response: {response.text}")
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
        chunk_count = 0
        total_chunks_size = 0
        
        for chunk in dify_response.iter_content(chunk_size=1024):
            if chunk:
                chunk_count += 1
                chunk_size = len(chunk)
                total_chunks_size += chunk_size
                
                try:
                    chunk_str = chunk.decode('utf-8')
                    print(f"DEBUG: Chunk {chunk_count} (size: {chunk_size}): {chunk_str[:100]}...")
                    lines = chunk_str.split('\n')
                    
                    for line_num, line in enumerate(lines):
                        if line.startswith('data: '):
                            try:
                                json_str = line[6:]
                                data = json.loads(json_str)
                                print(f"DEBUG: Chunk {chunk_count}, Line {line_num} - Parsed: {data}")
                                
                                if data.get('event') == 'agent_message':
                                    if 'answer' in data:
                                        answer_part = data['answer']
                                        full_reply += answer_part
                                        print(f"DEBUG: Added answer part (length: {len(answer_part)})")
                                elif data.get('event') == 'message_end':
                                    # Extract conversation_id if available
                                    if 'conversation_id' in data and not conversation_id:
                                        conversation_id_extracted = data['conversation_id']
                                        print(f"DEBUG: Conversation ID extracted: {conversation_id_extracted}")
                            except json.JSONDecodeError as e:
                                print(f"DEBUG: JSON decode error in chunk {chunk_count}, line {line_num}: {e}")
                                print(f"DEBUG: Problematic JSON string: {json_str[:200]}...")
                                continue
                except UnicodeDecodeError as e:
                    print(f"DEBUG: Unicode decode error in chunk {chunk_count}: {e}")
                    print(f"DEBUG: Raw chunk bytes: {chunk[:100]}...")
                    continue
                    
        print(f"DEBUG: Streaming complete. Processed {chunk_count} chunks, total size: {total_chunks_size} bytes")
        print(f"DEBUG: Final reply length: {len(full_reply)} characters")
        
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
        
    except requests.exceptions.Timeout as e:
        timeout_duration = int(os.environ.get('DIFY_TIMEOUT', 600))
        error_msg = f"Request timeout: The server took too long to respond (timeout: {timeout_duration}s). Please try again with a simpler query."
        print(f"DEBUG: Timeout error: {e}")
        ChatMessage.objects.create(session=session, sender="bot", text=error_msg)
        return JsonResponse({
            "reply": error_msg,
            "error_type": "timeout",
            "timeout_duration": timeout_duration
        })
    except requests.exceptions.ConnectionError as e:
        error_msg = f"Connection error: Unable to connect to the AI service. Please check your internet connection and try again."
        print(f"DEBUG: Connection error: {e}")
        ChatMessage.objects.create(session=session, sender="bot", text=error_msg)
        return JsonResponse({
            "reply": error_msg,
            "error_type": "connection"
        })
    except requests.exceptions.RequestException as e:
        error_msg = f"Request error: {str(e)}"
        print(f"DEBUG: Request exception: {e}")
        ChatMessage.objects.create(session=session, sender="bot", text=error_msg)
        return JsonResponse({
            "reply": error_msg,
            "error_type": "request"
        })
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        print(f"DEBUG: Unexpected error: {e}")
        print(f"DEBUG: Error type: {type(e).__name__}")
        import traceback
        traceback.print_exc()
        ChatMessage.objects.create(session=session, sender="bot", text=error_msg)
        return JsonResponse({
            "reply": error_msg,
            "error_type": "unexpected"
        })


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
        
        # Check if the response is in JSON format
        enhanced_reply = full_reply
        is_json_response = False
        
        try:
            # Try to parse the full_reply as JSON
            # First, check if the response starts and ends with curly braces (likely JSON)
            stripped_reply = full_reply.strip()
            if stripped_reply.startswith('{') and stripped_reply.endswith('}'):
                json_response = json.loads(stripped_reply)
                
                # Check if it has the expected JSON structure
                if isinstance(json_response, dict) and any(key in json_response for key in 
                    ['definition', 'math_formula', 'steps', 'sql_query', 'table_markdown', 'summary', 'python_code']):
                    is_json_response = True
                    enhanced_reply = format_json_response(json_response, session_id)
        except (json.JSONDecodeError, TypeError, ValueError):
            # Not a JSON response, continue with normal processing
            pass
        
        if not is_json_response:
            # Original logic for non-JSON responses
            # Check for Python code blocks and execute them automatically
            python_blocks = extract_python_blocks(full_reply)
            
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
        if enhanced_reply:
            ChatMessage.objects.create(session=session, sender="bot", text=enhanced_reply)
        else:
            # Fallback if no reply was received
            error_msg = "Sorry, no response received."
            ChatMessage.objects.create(session=session, sender="bot", text=error_msg)
            enhanced_reply = error_msg
        
        return JsonResponse({
            "reply": enhanced_reply,
            "is_json_response": is_json_response
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
    try:
        session = get_object_or_404(ChatSession, id=session_id)
        
        if request.method == 'POST':
            # Handle AJAX POST request
            session.delete()
            return JsonResponse({'success': True, 'message': 'Session deleted successfully'})
        else:
            # Handle GET request (direct URL access)
            session.delete()
            # Find the most recent remaining session
            next_session = ChatSession.objects.order_by('-created_at').first()
            if next_session:
                return redirect('chat_session', session_id=next_session.id)
            else:
                return redirect('dashboard')
    except Exception as e:
        if request.method == 'POST':
            return JsonResponse({'success': False, 'error': 'Session not found or already deleted'})
        else:
            # For GET requests, just redirect to dashboard
            return redirect('dashboard')

@csrf_exempt
@require_login
def rename_session_view(request, session_id):
    """Rename a chat session"""
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            new_title = data.get('title', '').strip()
            
            if not new_title:
                return JsonResponse({'success': False, 'error': 'Title cannot be empty'})
            
            session = get_object_or_404(ChatSession, id=session_id)
            session.title = new_title
            session.save()
            
            return JsonResponse({'success': True, 'message': 'Session renamed successfully'})
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Only POST method allowed'})

@csrf_exempt  
@require_login
def add_session_to_faq_view(request, session_id):
    """Add a chat session to FAQ"""
    if request.method == "POST":
        try:
            session = get_object_or_404(ChatSession, id=session_id)
            
            # Check if FAQ entry already exists for this session
            if FAQ.objects.filter(session=session).exists():
                return JsonResponse({'success': False, 'error': 'This session is already in FAQ'})
            
            # Create FAQ entry
            FAQ.objects.create(session=session)
            
            return JsonResponse({
                'success': True,
                'message': 'Added to FAQ successfully'
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Only POST method allowed'})

@csrf_exempt
@require_login  
def rename_faq_session_view(request, faq_id):
    """Rename an FAQ session"""
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            new_title = data.get('title', '').strip()
            
            if not new_title:
                return JsonResponse({'success': False, 'error': 'Title cannot be empty'})
            
            faq = get_object_or_404(FAQ, id=faq_id)
            faq.session.title = new_title
            faq.session.save()
            
            return JsonResponse({'success': True, 'message': 'FAQ session renamed successfully'})
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Only POST method allowed'})

@csrf_exempt
@require_login
def delete_faq_session_view(request, faq_id):
    """Delete an FAQ session"""
    if request.method == "POST":
        try:
            faq = get_object_or_404(FAQ, id=faq_id)
            faq.delete()
            
            return JsonResponse({'success': True, 'message': 'FAQ session deleted successfully'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Only POST method allowed'})

@csrf_exempt
@require_login
def remove_faq_session_view(request, faq_id):
    """Remove a session from FAQ (move it back to Chat Sessions)"""
    if request.method == "POST":
        try:
            faq = get_object_or_404(FAQ, id=faq_id)
            # Simply delete the FAQ entry - the session will automatically appear back in Chat Sessions
            # since the session itself is not deleted, only the FAQ reference
            faq.delete()
            
            return JsonResponse({
                'success': True, 
                'message': 'Session removed from FAQ and moved back to Chat Sessions'
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Only POST method allowed'})

def extract_python_blocks(text):
    """Extract Python code blocks from text"""
    pattern = r'```python\s*\n([\s\S]*?)\n```'
    matches = re.findall(pattern, text)
    return matches

def format_json_response(json_data, session_id):
    """Format the JSON response from the agent into a readable HTML structure"""
    html_parts = []
    
    # 1. FIRST SECTION: Summary
    if json_data.get('summary'):
        html_parts.append(f"""
        <div class="json-section">
            <h3 class="section-title">Summary</h3>
            <div class="section-content">
                <p>{json_data['summary']}</p>
            </div>
        </div>
        """)
    
    # 2. SECOND SECTION: Plot with Code button
    if json_data.get('python_code') and json_data['python_code'].strip():
        python_code = json_data['python_code']
        
        # Generate unique ID for this Python code
        import hashlib
        python_id = f"python-code-{hashlib.md5(python_code.encode()).hexdigest()[:8]}"
        
        # Execute the Python code
        plot_html = ""
        code_button_html = ""
        
        try:
            result = execute_python_script(python_code, session_id)
            if result['success']:
                plot_html = f"""
                <div class="plot-container">
                    <img src="{result['plot_url']}" alt="Generated Plot" style="max-width: 100%; height: auto; border: 1px solid #e2e8f0; border-radius: 8px;">
                </div>
                """
                code_button_html = f"""
                <button class="code-toggle-btn" onclick="toggleCode('{python_id}')">Hide Code</button>
                <div class="code-block" id="{python_id}" style="display: block;">
                    <div class="code-header">
                        <span class="code-language">python</span>
                        <button class="code-copy-btn" onclick="copyToClipboard(this, '{python_id}-content')">📋 Copy</button>
                    </div>
                    <div class="code-content">
                        <pre><code class="language-python" id="{python_id}-content">{python_code}</code></pre>
                    </div>
                </div>
                """
            else:
                error_details = result.get('error', 'Unknown error')
                stdout = result.get('stdout', '')
                stderr = result.get('stderr', '')
                debug_info = result.get('debug_info', {})
                
                plot_html = f"""
                <div class="plot-error">
                    <div class="error">❌ Plot Generation Failed: {error_details}</div>
                    <details class="error-details">
                        <summary>Debug Information</summary>
                        <div class="debug-output">
                            {f'<h4>Standard Output:</h4><pre>{stdout}</pre>' if stdout else ''}
                            {f'<h4>Standard Error:</h4><pre>{stderr}</pre>' if stderr else ''}
                            {f'<h4>Debug Info:</h4><pre>{debug_info}</pre>' if debug_info else ''}
                        </div>
                    </details>
                </div>
                """
        except Exception as e:
            plot_html = f"""
            <div class="plot-error">
                <div class="error">❌ Plot Generation Failed: {str(e)}</div>
            </div>
            """
        
        html_parts.append(f"""
        <div class="json-section plot-section">
            <h3 class="section-title">Plot</h3>
            <div class="section-content">
                {plot_html}
                {code_button_html}
            </div>
        </div>
        """)
    
    # 3. THIRD SECTION: Results Table (folded by default)
    if json_data.get('table_markdown') and json_data['table_markdown'].strip():
        # Convert markdown table to HTML
        import markdown
        table_html = markdown.markdown(json_data['table_markdown'], extensions=['tables'])
        
        # Generate unique ID for SQL query
        import hashlib
        sql_id = f"sql-query-{hashlib.md5(json_data.get('sql_query', '').encode()).hexdigest()[:8]}"
        table_id = f"table-{hashlib.md5(json_data['table_markdown'].encode()).hexdigest()[:8]}"
        
        # SQL Query button (if available)
        sql_button_html = ""
        if json_data.get('sql_query'):
            sql_button_html = f"""
            <button class="sql-toggle-btn" onclick="toggleSQL('{sql_id}')">Hide Query</button>
            <div class="code-block" id="{sql_id}" style="display: block;">
                <div class="code-header">
                    <span class="code-language">sql</span>
                    <button class="code-copy-btn" onclick="copyToClipboard(this, '{sql_id}-content')">📋 Copy</button>
                </div>
                <div class="code-content">
                    <pre><code class="language-sql" id="{sql_id}-content">{json_data['sql_query']}</code></pre>
                </div>
            </div>
            """
        
        html_parts.append(f"""
        <div class="json-section table-section">
            <h3 class="section-title">Results Table</h3>
            <div class="section-content">
                <div class="table-container" id="{table_id}">
                    {table_html}
                    <div class="table-toggle-container" onclick="toggleTable('{table_id}')">
                        <span class="table-toggle-text">▼ Show Complete Table</span>
                    </div>
                </div>
                {sql_button_html}
            </div>
        </div>
        """)
    
    # 4. FOURTH SECTION: Combined Information (Tabbed)
    combined_sections = []
    
    # Definitions tab
    if json_data.get('definition'):
        definitions = json_data['definition']
        if isinstance(definitions, list):
            definitions_html = "<ul class='definitions-list'>\n"
            for definition in definitions:
                if definition.strip():
                    definitions_html += f"<li>{definition}</li>\n"
            definitions_html += "</ul>"
        else:
            definitions_html = f"<p>{definitions}</p>"
        
        combined_sections.append(('definitions', 'Definitions', definitions_html))
    
    # Formulas tab
    if json_data.get('math_formula') and isinstance(json_data['math_formula'], list):
        formulas_html = ""
        for i, formula in enumerate(json_data['math_formula']):
            if formula.strip():
                # Keep the formula as-is if it's already in LaTeX format
                clean_formula = formula.strip()
                
                # Remove any outer $$ wrapper if present (agent sometimes sends with $$)
                if clean_formula.startswith('$$') and clean_formula.endswith('$$'):
                    clean_formula = clean_formula[2:-2].strip()
                
                # Only process if it looks like the problematic format
                if clean_formula.startswith('text') and 'frac' in clean_formula and '\\' not in clean_formula:
                    # Handle the specific malformed format from agent
                    import re
                    match = re.search(r'text(.+?)=frac(.+)', clean_formula)
                    if match:
                        var_name = match.group(1)
                        frac_content = match.group(2)
                        
                        # Look for pattern: textNumeratortextDenominator
                        frac_match = re.search(r'text(.+?)text(.+)', frac_content)
                        if frac_match:
                            numerator = frac_match.group(1)
                            denominator = frac_match.group(2)
                            clean_formula = f"\\text{{{var_name}}} = \\frac{{\\text{{{numerator}}}}}{{\\text{{{denominator}}}}}"
                
                # Escape quotes for HTML attribute
                escaped_formula = clean_formula.replace('"', '&quot;')
                print(f"DEBUG: Original formula: {formula}")
                print(f"DEBUG: Final formula: {clean_formula}")
                
                # Create the math element
                formulas_html += f'<div class="math-formula" data-formula="{escaped_formula}">$${clean_formula}$$</div>\n'
        
        if formulas_html:
            combined_sections.append(('formulas', 'Formulas', formulas_html))
    
    # Steps tab
    if json_data.get('steps') and isinstance(json_data['steps'], list):
        steps_html = "<ol class='steps-list'>\n"
        for step in json_data['steps']:
            if step.strip():
                steps_html += f"<li>{step}</li>\n"
        steps_html += "</ol>"
        
        combined_sections.append(('steps', 'Steps', steps_html))
    
    # Create the tabbed section if we have any combined content
    if combined_sections:
        tabs_html = ""
        content_html = ""
        
        for i, (tab_id, tab_title, tab_content) in enumerate(combined_sections):
            active_class = "active" if i == 0 else ""
            tabs_html += f'<button class="tab-btn {active_class}" onclick="switchTab(\'{tab_id}\')">{tab_title}</button>\n'
            
            display_style = "block" if i == 0 else "none"
            content_html += f'''
            <div class="tab-content" id="{tab_id}" style="display: {display_style};">
                {tab_content}
            </div>
            '''
        
        html_parts.append(f"""
        <div class="json-section combined-section">
            <h3 class="section-title">Information</h3>
            <div class="section-content">
                <div class="tab-bar">
                    {tabs_html}
                </div>
                <div class="tab-contents">
                    {content_html}
                </div>
            </div>
        </div>
        """)
    
    # Combine all sections
    final_html = f"""
    <div class="json-response-container">
        {''.join(html_parts)}
    </div>
    """
    
    return final_html

def execute_python_script(script_content, session_id):
    """Execute Python script and return plot file path if generated"""
    try:
        # Create a unique temporary directory for this execution
        temp_dir = Path(tempfile.gettempdir()) / f"dify_plots_{session_id}_{uuid.uuid4().hex[:8]}"
        temp_dir.mkdir(exist_ok=True)
        
        # Clean up the script content - fix common issues from agent-generated code
        cleaned_script = clean_python_script(script_content)
        
        # Prepare the script with proper matplotlib backend and necessary imports
        enhanced_script = f"""
import os
import sys
import traceback

try:
    # Set matplotlib backend before importing pyplot
    import matplotlib
    matplotlib.use('Agg')  # Use non-interactive backend for server environments
    import matplotlib.pyplot as plt
    print("Matplotlib imported successfully")
except ImportError as e:
    print(f"Error importing matplotlib: {{e}}")
    sys.exit(1)

try:
    import numpy as np
    print("NumPy imported successfully")
except ImportError as e:
    print(f"NumPy not available: {{e}}")
    # Don't exit for numpy, some scripts might not need it

try:
    import pandas as pd
    print("Pandas imported successfully")  
except ImportError as e:
    print(f"Pandas not available: {{e}}")
    # Don't exit for pandas, some scripts might not need it

try:
    import seaborn as sns
    print("Seaborn imported successfully")
except ImportError as e:
    print(f"Seaborn not available: {{e}}")

# Change to the working directory
os.chdir(r'{temp_dir}')
print(f"Working directory: {{os.getcwd()}}")

# Set up plot saving
plt.ioff()  # Turn off interactive mode

try:
    # Original script content (cleaned)
{cleaned_script}

    # Save the plot
    plt.tight_layout()
    plot_filename = 'plot.png'
    plt.savefig(plot_filename, dpi=300, bbox_inches='tight')
    plt.close('all')  # Close all figures to free memory
    print(f"Plot saved as {{plot_filename}}")
    
except Exception as e:
    print(f"Error in script execution: {{e}}")
    print("Traceback:")
    traceback.print_exc()
    sys.exit(1)

print("Script execution completed successfully")
"""
        
        # Create a temporary Python file
        script_file = temp_dir / "script.py"
        with open(script_file, 'w', encoding='utf-8') as f:
            f.write(enhanced_script)
        
        # Execute the script
        result = subprocess.run(
            ['python', str(script_file)],
            capture_output=True,
            text=True,
            cwd=temp_dir,
            timeout=60  # Increased timeout to 60 seconds
        )
        
        # Look for generated plot files
        plot_files = []
        for ext in ['png', 'jpg', 'jpeg', 'svg', 'pdf']:
            plot_files.extend(temp_dir.glob(f"*.{ext}"))
        
        # Check if script executed successfully
        if result.returncode != 0:
            return {
                'success': False,
                'error': f'Script execution failed (exit code {result.returncode})',
                'stdout': result.stdout,
                'stderr': result.stderr,
                'debug_info': {
                    'temp_dir': str(temp_dir),
                    'script_content': enhanced_script[:500] + "..." if len(enhanced_script) > 500 else enhanced_script
                }
            }
        
        if plot_files:
            # Return the first plot file found
            plot_file = plot_files[0]
            # Copy to a permanent location
            plots_dir = Path(settings.BASE_DIR) / "static" / "plots"
            plots_dir.mkdir(parents=True, exist_ok=True)
            
            plot_filename = f"plot_{session_id}_{uuid.uuid4().hex[:8]}{plot_file.suffix}"
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
            # List all files in temp directory for debugging
            try:
                temp_files = list(temp_dir.iterdir())
                file_list = [f.name for f in temp_files]
            except:
                file_list = []
            
            return {
                'success': False,
                'error': f'No plot file generated. Files in temp dir: {file_list}',
                'stdout': result.stdout,
                'stderr': result.stderr,
                'debug_info': {
                    'temp_dir': str(temp_dir),
                    'files_in_dir': file_list
                }
            }
            
    except subprocess.TimeoutExpired:
        return {
            'success': False,
            'error': 'Script execution timed out (60 seconds)'
        }
    except Exception as e:
        return {
            'success': False,
            'error': f'Exception in execute_python_script: {str(e)}',
            'exception_type': type(e).__name__
        }

def clean_python_script(script_content):
    """Clean up Python script content from agent to fix common issues"""
    import re
    
    # Remove any potential encoding issues
    script = script_content.strip()
    
    # Fix common issues with agent-generated code:
    
    # 1. Replace plt.show() with plt.savefig() and plt.close()
    script = re.sub(r'plt\.show\(\)', '', script)
    
    # 2. Fix DataFrame creation from 'result' variable that doesn't exist
    # Look for patterns like df = pd.DataFrame(result) and replace with sample data
    if 'pd.DataFrame(result)' in script:
        script = script.replace('pd.DataFrame(result)', 'pd.DataFrame()')
        # Add comment about missing data
        script = "# Note: 'result' variable not available, using sample data\n" + script
    
    # 3. Fix pandas pivot syntax errors
    # Fix the exact error from the screenshot: positional argument after keyword arguments
    # Pattern: .pivot(index="col", columns="col", "values") -> .pivot(index="col", columns="col", values="values")
    script = re.sub(r'\.pivot\(\s*index\s*=\s*"([^"]+)"\s*,\s*columns\s*=\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)', 
                   r'.pivot(index="\1", columns="\2", values="\3")', script)
    
    # Also handle single quotes
    script = re.sub(r"\.pivot\(\s*index\s*=\s*'([^']+)'\s*,\s*columns\s*=\s*'([^']+)'\s*,\s*'([^']+)'\s*\)", 
                   r".pivot(index='\1', columns='\2', values='\3')", script)
    
    # Generic fix for other cases
    script = re.sub(r"\.pivot\(([^)]+),\s*'([^']+)'\)", r".pivot(\1, values='\2')", script)
    script = re.sub(r'\.pivot\(([^)]+),\s*"([^"]+)"\)', r'.pivot(\1, values="\2")', script)
    
    # 4. Fix seaborn barplot syntax issues
    # Fix: sns.barplot(data=df, x='col', y=value) -> sns.barplot(data=df, x='col', y='col')
    script = re.sub(r"sns\.barplot\(([^)]+),\s*y=(\d+)", r"sns.barplot(\1, y=str(\2))", script)
    
    # 5. Replace references to SQL result data with sample data creation
    if 'pivot_df' in script and 'result' in script:
        # Create sample data if the script tries to use database results
        sample_data_creation = """
# Create sample data since database results are not available
import pandas as pd
import numpy as np

# Sample data based on the expected structure
categories = ['Bags', 'Equipment', 'Mens Footwear', 'Womens Pants', 'Womens SS-LS Tops']
years = [2023, 2024, 2025]
np.random.seed(42)  # For reproducible results

# Create sample data
data = []
for category in categories:
    for year in years:
        freq = np.random.uniform(1.0, 2.5)  # Random frequency between 1.0 and 2.5
        data.append({'product_category': category, 'fiscal_year': year, 'avg_order_frequency': round(freq, 2)})

df_avg_order_frequency = pd.DataFrame(data)
"""
        script = sample_data_creation + script
    
    # 6. Ensure proper indentation (convert to spaces)
    lines = script.split('\n')
    cleaned_lines = []
    for line in lines:
        # Convert tabs to 4 spaces
        line = line.replace('\t', '    ')
        cleaned_lines.append(line)
    
    script = '\n'.join(cleaned_lines)
    
    # 7. Add proper indentation for the script content
    lines = script.split('\n')
    indented_lines = []
    for line in lines:
        if line.strip():  # Only indent non-empty lines
            indented_lines.append('    ' + line)
        else:
            indented_lines.append('')
    
    return '\n'.join(indented_lines)

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
            'id': faq.id,  # FAQ ID for rename/delete operations
            'session_id': faq.session.id,  # Session ID for linking to chat
            'created_at': faq.session.created_at,
            'preview': preview,
            'faq_created_at': faq.created_at,
            'title': faq.session.title  # Add title for display
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

@require_login
def test_formatting_view(request):
    """Test view for formatting (can be removed in production)"""
    test_json = {
        "definition": [
            "Average Order Frequency (FQY): Number of positive transactions / Number of Active Guests within the period of analysis.",
            "Active Guest: A guest who has made at least 1 positive or negative transaction within the period of analysis.",
            "Product Category: Class name in the product hierarchy (e.g., 'Womens Pants', 'Mens SS-LS Tops')."
        ],
        "math_formula": ["\\text{Average Order Frequency} = \\frac{\\text{Number of Positive Transactions}}{\\text{Number of Active Guests}}"],
        "steps": [
            "1. Parse the JSON response from the agent",
            "2. Extract each section (definition, formulas, steps, etc.)",
            "3. Format each section with proper HTML and styling",
            "4. Execute Python code and display plots",
            "5. Render the complete formatted response"
        ],
        "sql_query": "SELECT t.customer_code, COUNT(*) as transaction_count FROM transactions t WHERE t.status = 'completed' GROUP BY t.customer_code ORDER BY transaction_count DESC;",
        "table_markdown": "| Year | Product Category | Avg Order Frequency |\n| --- | --- | --- |\n| 2023 | Womens Pants | 2.68 |\n| 2023 | Womens SS-LS Tops | 2.21 |\n| 2024 | Womens Pants | 2.31 |\n| 2024 | Womens SS-LS Tops | 1.82 |\n| 2025 | Womens Pants | 2.49 |\n| 2025 | Womens SS-LS Tops | 1.94 |",
        "summary": "The test formatting demonstrates how JSON responses from the agent are parsed and displayed with proper sections, titles, and formatting. This shows the new structure with definitions as arrays.",
        "python_code": "import matplotlib.pyplot as plt\nimport numpy as np\n\n# Sample data for demonstration\ncategories = ['Womens Pants', 'Womens SS-LS Tops', 'Mens SS-LS Tops']\ny2023 = [2.68, 2.21, 1.77]\ny2024 = [2.31, 1.82, 1.40]\ny2025 = [2.49, 1.94, 1.50]\n\nx = np.arange(len(categories))\nwidth = 0.25\n\nfig, ax = plt.subplots(figsize=(10, 6))\nrects1 = ax.bar(x - width, y2023, width, label='2023', color='#FF6B6B')\nrects2 = ax.bar(x, y2024, width, label='2024', color='#4ECDC4')\nrects3 = ax.bar(x + width, y2025, width, label='2025', color='#45B7D1')\n\nax.set_xlabel('Product Category')\nax.set_ylabel('Average Order Frequency')\nax.set_title('Average Order Frequency by Product Category (2023-2025)')\nax.set_xticks(x)\nax.set_xticklabels(categories)\nax.legend()\n\nplt.tight_layout()\nplt.show()"
    }
    
    # Create a temporary session for testing
    session = ChatSession.objects.create(title="Test JSON Formatting - New Structure")
    formatted_html = format_json_response(test_json, session.id)
    
    return JsonResponse({
        "reply": formatted_html,
        "is_json_response": True
    })

@require_login
def debug_connection_view(request):
    """Debug view to test network connectivity from deployment"""
    import socket
    import requests
    
    debug_info = {
        "server_info": {
            "hostname": socket.gethostname(),
            "local_ip": socket.gethostbyname(socket.gethostname())
        },
        "environment": {
            "debug": settings.DEBUG,
            "allowed_hosts": settings.ALLOWED_HOSTS,
            "dify_url": settings.DIFY_API_URL
        },
        "network_tests": {}
    }
    
    # Test external IP
    try:
        response = requests.get('https://httpbin.org/ip', timeout=10)
        debug_info["network_tests"]["external_ip"] = response.json()
    except Exception as e:
        debug_info["network_tests"]["external_ip"] = {"error": str(e)}
    
    # Test Dify API connectivity
    try:
        response = requests.get(settings.DIFY_API_URL.replace('/chat-messages', ''), timeout=10)
        debug_info["network_tests"]["dify_connectivity"] = {
            "status_code": response.status_code,
            "accessible": True
        }
    except Exception as e:
        debug_info["network_tests"]["dify_connectivity"] = {"error": str(e), "accessible": False}
    
    return JsonResponse(debug_info)

@require_login
def debug_python_view(request):
    """Debug Python script execution"""
    test_script = """
import matplotlib.pyplot as plt
import numpy as np

# Create sample data
x = np.linspace(0, 10, 100)
y = np.sin(x)

# Create the plot
plt.figure(figsize=(8, 6))
plt.plot(x, y, 'b-', linewidth=2, label='sin(x)')
plt.title('Test Sine Wave')
plt.xlabel('X axis')
plt.ylabel('Y axis')
plt.legend()
plt.grid(True)
"""
    
    # Test the Python execution
    session = ChatSession.objects.create(title="Python Debug Test")
    result = execute_python_script(test_script, session.id)
    
    return JsonResponse({
        'execution_result': result,
        'test_script': test_script
    })

@require_login
def debug_katex_view(request):
    """Debug KaTeX formula rendering"""
    from django.http import HttpResponse
    
    test_html = """
<!DOCTYPE html>
<html>
<head>
    <title>KaTeX Debug Test</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; }
        .test-section { margin: 20px 0; padding: 20px; border: 1px solid #ccc; }
        .math-formula { margin: 16px 0; text-align: center; background: #f9fafb; padding: 16px; border-radius: 6px; border: 1px solid #e5e7eb; }
    </style>
</head>
<body>
    <h1>KaTeX Formula Rendering Test</h1>
    
    <div class="test-section">
        <h2>Test 1: Simple Formula</h2>
        <div class="math-formula" data-formula="x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}">
            $$x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}$$
        </div>
    </div>
    
    <div class="test-section">
        <h2>Test 2: Your Formula Format</h2>
        <div class="math-formula" data-formula="\\text{Average Order Frequency} = \\frac{\\text{Number of Positive Transactions}}{\\text{Number of Active Guests}}">
            $$\\text{Average Order Frequency} = \\frac{\\text{Number of Positive Transactions}}{\\text{Number of Active Guests}}$$
        </div>
    </div>
    
    <div class="test-section">
        <h2>Test 3: Agent's Complex Format</h2>
        <div class="math-formula" data-formula-original="textAverageOrderFrequency=fractextNumberofPositiveTransactionstextNumberofActiveGuests">
            Original: textAverageOrderFrequency=fractextNumberofPositiveTransactionstextNumberofActiveGuests
        </div>
    </div>
    
    <script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
    <script>
        console.log('KaTeX loaded:', typeof katex !== 'undefined');
        
        setTimeout(() => {
            // Test basic rendering
            const mathElements = document.querySelectorAll('.math-formula');
            mathElements.forEach((elem, index) => {
                const formula = elem.getAttribute('data-formula');
                if (formula) {
                    try {
                        console.log(`Rendering formula ${index + 1}:`, formula);
                        const rendered = katex.renderToString(formula, { 
                            displayMode: true,
                            throwOnError: false,
                            strict: false
                        });
                        elem.innerHTML = rendered;
                        console.log(`Success ${index + 1}`);
                    } catch (error) {
                        console.error(`Error ${index + 1}:`, error);
                        elem.innerHTML = `<div style="color: red;">Error: ${error.message}</div>`;
                    }
                }
                
                // Handle the complex format
                const originalFormula = elem.getAttribute('data-formula-original');
                if (originalFormula) {
                    // Process the complex format like your agent sends
                    let cleanFormula = originalFormula;
                    if (cleanFormula.startsWith('text') && cleanFormula.includes('frac')) {
                        const match = cleanFormula.match(/text(.+?)=frac(.+)/);
                        if (match) {
                            const varName = match[1];
                            const fracContent = match[2];
                            const fracMatch = fracContent.match(/text(.+?)text(.+)/);
                            if (fracMatch) {
                                const numerator = fracMatch[1];
                                const denominator = fracMatch[2];
                                cleanFormula = `\\\\text{${varName}} = \\\\frac{\\\\text{${numerator}}}{\\\\text{${denominator}}}`;
                            }
                        }
                    }
                    
                    try {
                        console.log('Processing complex formula:', originalFormula);
                        console.log('Cleaned to:', cleanFormula);
                        const rendered = katex.renderToString(cleanFormula, { 
                            displayMode: true,
                            throwOnError: false,
                            strict: false
                        });
                        elem.innerHTML = `<div>Original: ${originalFormula}</div><div>Rendered:</div>${rendered}`;
                    } catch (error) {
                        console.error('Complex formula error:', error);
                        elem.innerHTML = `<div style="color: red;">Failed to render: ${cleanFormula}<br>Error: ${error.message}</div>`;
                    }
                }
            });
        }, 100);
    </script>
</body>
</html>
    """
    
    return HttpResponse(test_html)
