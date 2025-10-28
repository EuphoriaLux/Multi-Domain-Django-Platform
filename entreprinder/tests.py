from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from .models import EntrepreneurProfile, Industry
from matching.models import Like, Match

class EntrepreneurProfileTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='12345')
        self.industry = Industry.objects.create(name='Tech')
        self.profile = EntrepreneurProfile.objects.create(
            user=self.user,
            bio='Test bio',
            company='Test Company',
            industry=self.industry,
            location='Test City'
        )

    def test_profile_creation(self):
        self.assertEqual(self.profile.user.username, 'testuser')
        self.assertEqual(self.profile.bio, 'Test bio')

class ViewsTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='testuser', password='12345')
        self.industry = Industry.objects.create(name='Tech')
        self.profile = EntrepreneurProfile.objects.create(
            user=self.user,
            bio='Test bio',
            company='Test Company',
            industry=self.industry,
            location='Test City'
        )

    def test_home_view(self):
        response = self.client.get(reverse('entreprinder:home'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'landing_page.html')

    def test_profile_view(self):
        self.client.login(username='testuser', password='12345')
        response = self.client.get(reverse('entreprinder:profile'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'profile.html')

class MatchingTestCase(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username='user1', password='12345')
        self.user2 = User.objects.create_user(username='user2', password='12345')
        self.industry = Industry.objects.create(name='Tech')
        self.profile1 = EntrepreneurProfile.objects.create(user=self.user1, industry=self.industry)
        self.profile2 = EntrepreneurProfile.objects.create(user=self.user2, industry=self.industry)

    def test_like_and_match(self):
        # Create likes
        Like.objects.create(liker=self.profile1, liked=self.profile2)
        Like.objects.create(liker=self.profile2, liked=self.profile1)
        
        # Check if a match was created
        match = Match.objects.filter(
            entrepreneur1__in=[self.profile1, self.profile2],
            entrepreneur2__in=[self.profile1, self.profile2]
        ).first()
        
        # Debug print statements
        print(f"Likes from profile1 to profile2: {Like.objects.filter(liker=self.profile1, liked=self.profile2).exists()}")
        print(f"Likes from profile2 to profile1: {Like.objects.filter(liker=self.profile2, liked=self.profile1).exists()}")
        print(f"All matches: {list(Match.objects.all())}")
        
        self.assertIsNotNone(match, "No match was created after mutual likes")

    def test_match_creation_logic(self):
        # This test checks if the match creation logic is working properly
        Like.objects.create(liker=self.profile1, liked=self.profile2)
        self.assertEqual(Match.objects.count(), 0, "Match shouldn't be created after only one like")
        
        Like.objects.create(liker=self.profile2, liked=self.profile1)
        self.assertEqual(Match.objects.count(), 1, "Match should be created after mutual likes")