from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, Button, Div
from django import forms

class SwipeForm(forms.Form):
    entrepreneur_id = forms.IntegerField(widget=forms.HiddenInput())
    action = forms.CharField(widget=forms.HiddenInput(), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_id = 'swipe-form'
        self.helper.form_method = 'post'
        self.helper.layout = Layout(
            'entrepreneur_id',
            'action',
            Div(
                Button('dislike', '👎 Dislike', css_class='btn btn-danger mr-2', onclick="submitForm('dislike')", id='dislike-btn'),
                Button('like', '👍 Like', css_class='btn btn-success', onclick="submitForm('like')", id='like-btn'),
                css_class='d-flex justify-content-between mt-3'
            )
        )

    def clean(self):
        cleaned_data = super().clean()
        action = cleaned_data.get('action')
        if action not in ['dislike', 'like']:
            raise forms.ValidationError("Invalid action")
        return cleaned_data