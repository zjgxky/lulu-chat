import os
from django.conf import settings
from django.templatetags.static import static
import time

from .models import FAQ

def logo_processor(request):
    logo_url = static('Lululemon-Emblem-700x394.png')
    custom_logo_path = os.path.join(settings.BASE_DIR, 'static', 'custom_logo.png')
    if os.path.exists(custom_logo_path):
        logo_url = static('custom_logo.png') + f'?v={int(time.time())}'
        
    return {'logo_url': logo_url}

def faq_sessions(request):
    """Context processor to provide FAQ sessions to all templates"""
    if request.session.get('is_authenticated'):
        faq_sessions_queryset = FAQ.objects.order_by('-created_at')
        faq_sessions = []
        for faq in faq_sessions_queryset:
            first_user_msg = faq.session.messages.filter(sender="user").order_by('timestamp').first()
            preview = first_user_msg.text if first_user_msg else None
            faq_sessions.append({
                'id': faq.id,
                'session_id': faq.session.id,
                'created_at': faq.session.created_at,
                'preview': preview,
                'faq_created_at': faq.created_at,
                'title': faq.session.title
            })
        return {'faq_sessions': faq_sessions}
    return {'faq_sessions': []}
