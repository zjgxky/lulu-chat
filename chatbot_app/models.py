from django.db import models

# Create your models here.

class ChatSession(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    conversation_id = models.CharField(max_length=255, blank=True, null=True)
    title = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        if self.title:
            return f"{self.title}"
        return f"Session {self.id} - {self.created_at.strftime('%Y-%m-%d %H:%M:%S')}"

class ChatMessage(models.Model):
    session = models.ForeignKey(ChatSession, related_name='messages', on_delete=models.CASCADE)
    sender = models.CharField(max_length=10)  # 'user' or 'bot'
    text = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.sender}: {self.text[:30]}..."

class ChatFeedback(models.Model):
    FEEDBACK_CHOICES = [
        ('like', 'Like'),
        ('dislike', 'Dislike'),
    ]
    
    session = models.ForeignKey(ChatSession, related_name='feedbacks', on_delete=models.CASCADE)
    message = models.ForeignKey(ChatMessage, related_name='feedbacks', on_delete=models.CASCADE, null=True, blank=True)
    feedback_type = models.CharField(max_length=10, choices=FEEDBACK_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.session.id} - {self.feedback_type} - {self.created_at.strftime('%Y-%m-%d %H:%M:%S')}"

class FAQ(models.Model):
    session = models.ForeignKey(ChatSession, related_name='faqs', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"FAQ Session {self.session.id} - {self.created_at.strftime('%Y-%m-%d %H:%M:%S')}"

    class Meta:
        ordering = ['-created_at']
