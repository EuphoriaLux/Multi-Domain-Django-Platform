from allauth.account.forms import SignupForm
from django import forms
from .models import EntrepreneurProfile
from .widgets import AdminImageWidget
import logging


logger = logging.getLogger(__name__)


class EntrepreneurProfileForm(forms.ModelForm):
    class Meta:
        model = EntrepreneurProfile
        fields = ['profile_picture', 'bio', 'company', 'industry', 'looking_for', 'location']
        widgets = {
            'bio': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'company': forms.TextInput(attrs={'class': 'form-control'}),
            'industry': forms.TextInput(attrs={'class': 'form-control'}),
            'looking_for': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'location': forms.TextInput(attrs={'class': 'form-control'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['profile_picture'].widget = AdminImageWidget()


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
    


