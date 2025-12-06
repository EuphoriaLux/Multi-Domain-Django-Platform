from allauth.account.forms import SignupForm
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Button, Div
from django import forms
from .models import EntrepreneurProfile
import logging

logger = logging.getLogger(__name__)


class EntrepreneurProfileAdminForm(forms.ModelForm):
    class Meta:
        model = EntrepreneurProfile
        fields = '__all__'


class EntrepreneurProfileForm(forms.ModelForm):
    class Meta:
        model = EntrepreneurProfile
        fields = ['bio', 'company', 'industry', 'looking_for', 'location']
        widgets = {
            'bio': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'company': forms.TextInput(attrs={'class': 'form-control'}),
            'industry': forms.TextInput(attrs={'class': 'form-control'}),
            'looking_for': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'location': forms.TextInput(attrs={'class': 'form-control'}),
        }


class CustomSignupForm(SignupForm):
    company = forms.CharField(max_length=100, required=True)

    def __init__(self, *args, **kwargs):
        super(CustomSignupForm, self).__init__(*args, **kwargs)
        self.fields['email'].widget.attrs.update({'autofocus': 'autofocus'})
        if 'username' in self.fields:
            del self.fields['username']

    def save(self, request):
        logger.info("CustomSignupForm save method called")
        user = super(CustomSignupForm, self).save(request)
        logger.info(f"User created with email: {user.email}")
        try:
            EntrepreneurProfile.objects.create(
                user=user,
                company=self.cleaned_data['company']
            )
            logger.info(f"EntrepreneurProfile created for user with email: {user.email}")
        except Exception as e:
            logger.error(f"Error creating EntrepreneurProfile: {str(e)}")
        return user


# =============================================================================
# Matching Forms (merged from matching app)
# =============================================================================

class SwipeForm(forms.Form):
    """Form for handling swipe actions (like/dislike)."""
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
                Button('dislike', 'Dislike', css_class='btn btn-danger mr-2', onclick="submitForm('dislike')", id='dislike-btn'),
                Button('like', 'Like', css_class='btn btn-success', onclick="submitForm('like')", id='like-btn'),
                css_class='d-flex justify-content-between mt-3'
            )
        )

    def clean(self):
        cleaned_data = super().clean()
        action = cleaned_data.get('action')
        if action not in ['dislike', 'like']:
            raise forms.ValidationError("Invalid action")
        return cleaned_data
    


