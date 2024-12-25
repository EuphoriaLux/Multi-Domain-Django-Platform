import os
from django import forms
from django.conf import settings
from django.utils.html import format_html
from django.utils.safestring import mark_safe

class AdminImageWidget(forms.widgets.Widget):
    template_name = 'entreprinder/widgets/admin_image_widget.html'

    def __init__(self, attrs=None):
        super().__init__(attrs)
        self.media_files = self.get_media_files()

    def get_media_files(self):
        media_root = settings.MEDIA_ROOT
        if not os.path.exists(media_root):
            return []
        
        image_files = []
        for root, _, files in os.walk(media_root):
            for file in files:
                if file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                    relative_path = os.path.relpath(os.path.join(root, file), media_root)
                    image_files.append(relative_path.replace('\\', '/'))
        return image_files

    def format_value(self, value):
        if value and value not in self.media_files and value:
            return ''
        return value

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['widget']['media_files'] = self.media_files
        context['widget']['selected_image'] = value
        return context
    
    def render(self, name, value, attrs=None, renderer=None):
        context = self.get_context(name, value, attrs)
        return mark_safe(format_html(
            '<div class="admin-image-widget" >'
            '<select name="{}" class="form-select">',
            name
        ) + ''.join(
            format_html(
                '<option value="{}" {}>{}</option>',
                file,
                'selected' if file == context['widget']['selected_image'] else '',
                file
            ) for file in context['widget']['media_files']
        ) + format_html(
            '</select>'
            '</div>'
        ))
