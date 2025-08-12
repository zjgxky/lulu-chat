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
    
    # Get timeout from settings
    timeout = getattr(settings, 'DIFY_TIMEOUT', 30)
    
    if response_mode == "streaming":
        # For streaming, return the response object directly
        response = requests.post(url, headers=headers, json=payload, stream=True, timeout=timeout)
        return response
    else:
        # For blocking, return JSON response
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
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
        
        # Check if the response is in JSON format
        enhanced_reply = full_reply
        is_json_response = False
        
        try:
            # Try to parse the full_reply as JSON
            json_response = json.loads(full_reply.strip())
            
            # Check if it has the expected JSON structure
            if isinstance(json_response, dict) and any(key in json_response for key in 
                ['definition', 'math_formula', 'steps', 'sql_query', 'table_markdown', 'summary', 'python_code']):
                is_json_response = True
                enhanced_reply = format_json_response(json_response, session_id)
        except (json.JSONDecodeError, TypeError):
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

def format_json_response(json_data, session_id):
    """Format the JSON response from the agent into a readable HTML structure"""
    html_parts = []
    
    # Definition section
    if json_data.get('definition'):
        html_parts.append(f"""
        <div class="json-section">
            <h3 class="section-title">📖 Definition</h3>
            <div class="section-content">
                <p>{json_data['definition']}</p>
            </div>
        </div>
        """)
    
    # Math formula section
    if json_data.get('math_formula') and isinstance(json_data['math_formula'], list):
        formulas_html = ""
        for i, formula in enumerate(json_data['math_formula']):
            if formula.strip():
                # Clean up the formula - remove extra escaping
                clean_formula = formula.replace('\\n', '').replace('\\t', '').strip()
                formulas_html += f'<div class="math-formula" data-formula="{clean_formula}">$${clean_formula}$$</div>\n'
        
        if formulas_html:
            html_parts.append(f"""
            <div class="json-section">
                <h3 class="section-title">🧮 Mathematical Formulas</h3>
                <div class="section-content">
                    {formulas_html}
                </div>
            </div>
            """)
    
    # Steps section
    if json_data.get('steps') and isinstance(json_data['steps'], list):
        steps_html = "<ol class='steps-list'>\n"
        for step in json_data['steps']:
            if step.strip():
                steps_html += f"<li>{step}</li>\n"
        steps_html += "</ol>"
        
        html_parts.append(f"""
        <div class="json-section">
            <h3 class="section-title">📋 Analysis Steps</h3>
            <div class="section-content">
                {steps_html}
            </div>
        </div>
        """)
    
    # SQL Query section
    if json_data.get('sql_query'):
        html_parts.append(f"""
        <div class="json-section">
            <h3 class="section-title">💾 SQL Query</h3>
            <div class="section-content">
                <div class="code-block">
                    <div class="code-header">
                        <span class="code-language">sql</span>
                        <button class="code-copy-btn" onclick="copyToClipboard(this, 'sql-query')">📋 Copy</button>
                    </div>
                    <div class="code-content">
                        <pre><code class="language-sql" id="sql-query">{json_data['sql_query']}</code></pre>
                    </div>
                </div>
            </div>
        </div>
        """)
    
    # Table section
    if json_data.get('table_markdown') and json_data['table_markdown'].strip():
        html_parts.append(f"""
        <div class="json-section">
            <h3 class="section-title">📊 Results Table</h3>
            <div class="section-content">
                <div class="table-container">
                    {json_data['table_markdown']}
                </div>
            </div>
        </div>
        """)
    
    # Summary section
    if json_data.get('summary'):
        html_parts.append(f"""
        <div class="json-section">
            <h3 class="section-title">📈 Summary</h3>
            <div class="section-content">
                <p>{json_data['summary']}</p>
            </div>
        </div>
        """)
    
    # Python code section - execute and show plot
    if json_data.get('python_code') and json_data['python_code'].strip():
        python_code = json_data['python_code']
        
        # Execute the Python code
        plot_html = ""
        try:
            result = execute_python_script(python_code, session_id)
            if result['success']:
                plot_html = f"""
                <div class="auto-plot-display">
                    <div class="plot-title">Generated Visualization</div>
                    <div class="plot-container">
                        <img src="{result['plot_url']}" alt="Generated Plot" style="max-width: 100%; height: auto; border: 1px solid #e2e8f0; border-radius: 8px;">
                        <button class="download-plot-btn" onclick="downloadPlot('{result['plot_url']}', '{result['plot_filename']}')">📥 Download Plot</button>
                    </div>
                </div>
                """
            else:
                plot_html = f"""
                <div class="plot-error">
                    <div class="error">❌ Plot Generation Failed: {result.get('error', 'Unknown error')}</div>
                </div>
                """
        except Exception as e:
            plot_html = f"""
            <div class="plot-error">
                <div class="error">❌ Plot Generation Failed: {str(e)}</div>
            </div>
            """
        
        html_parts.append(f"""
        <div class="json-section">
            <h3 class="section-title">🐍 Python Code & Visualization</h3>
            <div class="section-content">
                <div class="code-block">
                    <div class="code-header">
                        <span class="code-language">python</span>
                        <button class="code-copy-btn" onclick="copyToClipboard(this, 'python-code')">📋 Copy</button>
                    </div>
                    <div class="code-content">
                        <pre><code class="language-python" id="python-code">{python_code}</code></pre>
                    </div>
                </div>
                {plot_html}
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
        
        # Prepare the script with proper matplotlib backend and necessary imports
        enhanced_script = f"""
import os
import sys

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
except ImportError:
    print("NumPy not available")

try:
    import pandas as pd
    print("Pandas imported successfully")  
except ImportError:
    print("Pandas not available")

# Change to the working directory
os.chdir(r'{temp_dir}')
print(f"Working directory: {{os.getcwd()}}")

# Original script content
{script_content}

print("Script execution completed")
"""
        
        # Create a temporary Python file
        script_file = temp_dir / "script.py"
        with open(script_file, 'w') as f:
            f.write(enhanced_script)
        
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
        
        # Check if script executed successfully
        if result.returncode != 0:
            return {
                'success': False,
                'error': f'Script execution failed (exit code {result.returncode})',
                'stdout': result.stdout,
                'stderr': result.stderr
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

@require_login
def test_formatting_view(request):
    """Test view for formatting (can be removed in production)"""
    test_json = {
        "definition": "This is a test definition to verify that the JSON formatting works correctly.",
        "math_formula": ["FQY = \\frac{\\text{Number of positive transactions}}{\\text{Number of Active Guests}}"],
        "steps": [
            "1. Parse the JSON response from the agent",
            "2. Extract each section (definition, formulas, steps, etc.)",
            "3. Format each section with proper HTML and styling",
            "4. Execute Python code and display plots",
            "5. Render the complete formatted response"
        ],
        "sql_query": "SELECT t.customer_code, COUNT(*) as transaction_count FROM transactions t WHERE t.status = 'completed' GROUP BY t.customer_code ORDER BY transaction_count DESC;",
        "table_markdown": "| Column | Value | Description |\n| --- | --- | --- |\n| FY2023 | 1.50 | Average frequency |\n| FY2024 | 1.65 | Improved performance |\n| FY2025 | 1.75 | Projected growth |",
        "summary": "The test formatting demonstrates how JSON responses from the agent are parsed and displayed with proper sections, titles, and formatting.",
        "python_code": "import matplotlib.pyplot as plt\nimport numpy as np\n\nx = np.linspace(0, 10, 100)\ny = np.sin(x)\n\nplt.figure(figsize=(10, 6))\nplt.plot(x, y, 'b-', linewidth=2, label='sin(x)')\nplt.title('Test Plot Generation')\nplt.xlabel('X axis')\nplt.ylabel('Y axis')\nplt.legend()\nplt.grid(True)\nplt.show()"
    }
    
    # Create a temporary session for testing
    session = ChatSession.objects.create(title="Test JSON Formatting")
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
    
    return JsonResponse(debug_info, indent=2)
