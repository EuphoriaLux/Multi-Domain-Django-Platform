from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
import json
from unittest.mock import patch
from .models import PixelCanvas, Pixel, PixelHistory, UserPixelCooldown, UserPixelStats


class PixelCanvasModelTest(TestCase):
    """Test the PixelCanvas model and related models"""
    
    def setUp(self):
        self.canvas = PixelCanvas.objects.create(
            name="Test Canvas",
            width=50,
            height=50,
            anonymous_cooldown_seconds=30,
            registered_cooldown_seconds=12,
            anonymous_pixels_per_minute=2,
            registered_pixels_per_minute=5
        )
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
    
    def test_canvas_creation(self):
        """Test canvas is created with correct attributes"""
        self.assertEqual(self.canvas.name, "Test Canvas")
        self.assertEqual(self.canvas.width, 50)
        self.assertEqual(self.canvas.height, 50)
        self.assertTrue(self.canvas.is_active)
        self.assertEqual(self.canvas.anonymous_cooldown_seconds, 30)
        self.assertEqual(self.canvas.registered_cooldown_seconds, 12)
    
    def test_pixel_creation(self):
        """Test pixel creation and unique constraint"""
        pixel = Pixel.objects.create(
            canvas=self.canvas,
            x=10,
            y=10,
            color="#FF0000",
            placed_by=self.user
        )
        self.assertEqual(pixel.x, 10)
        self.assertEqual(pixel.y, 10)
        self.assertEqual(pixel.color, "#FF0000")
        self.assertEqual(pixel.placed_by, self.user)
        
        # Test unique constraint
        with self.assertRaises(Exception):
            Pixel.objects.create(
                canvas=self.canvas,
                x=10,
                y=10,
                color="#00FF00",
                placed_by=self.user
            )
    
    def test_pixel_history(self):
        """Test pixel history tracking"""
        PixelHistory.objects.create(
            canvas=self.canvas,
            x=5,
            y=5,
            color="#0000FF",
            placed_by=self.user
        )
        
        history = PixelHistory.objects.filter(canvas=self.canvas)
        self.assertEqual(history.count(), 1)
        self.assertEqual(history.first().color, "#0000FF")
    
    def test_user_cooldown_methods(self):
        """Test cooldown model methods"""
        # Test for registered user
        cooldown = UserPixelCooldown.objects.create(
            user=self.user,
            canvas=self.canvas
        )
        self.assertEqual(cooldown.get_cooldown_seconds(), 12)
        self.assertEqual(cooldown.get_max_pixels_per_minute(), 5)
        
        # Test for anonymous user
        anon_cooldown = UserPixelCooldown.objects.create(
            user=None,
            canvas=self.canvas,
            session_key="test_session"
        )
        self.assertEqual(anon_cooldown.get_cooldown_seconds(), 30)
        self.assertEqual(anon_cooldown.get_max_pixels_per_minute(), 2)
    
    def test_user_stats(self):
        """Test user statistics tracking"""
        stats = UserPixelStats.objects.create(
            user=self.user,
            canvas=self.canvas,
            total_pixels_placed=10
        )
        self.assertEqual(stats.total_pixels_placed, 10)
        self.assertEqual(str(stats), "testuser - 10 pixels")


class PixelWarViewTest(TestCase):
    """Test the pixel war views"""
    
    def setUp(self):
        self.client = Client()
        self.canvas = PixelCanvas.objects.create(
            name="Lux Pixel War",
            width=100,
            height=100
        )
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
    
    def test_pixel_war_view(self):
        """Test the main pixel war page loads"""
        response = self.client.get(reverse('vibe_coding:pixel_war'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Lux Pixel War')
        self.assertTemplateUsed(response, 'vibe_coding/pixel_war.html')
    
    def test_pixel_war_view_authenticated(self):
        """Test the pixel war page for authenticated users"""
        self.client.login(username='testuser', password='testpass123')
        response = self.client.get(reverse('vibe_coding:pixel_war'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'testuser')
        self.assertContains(response, '5 pixels/min')  # Registered user rate
    
    def test_canvas_state_api(self):
        """Test the canvas state API endpoint"""
        # Create some pixels
        Pixel.objects.create(
            canvas=self.canvas,
            x=10,
            y=10,
            color="#FF0000"
        )
        
        response = self.client.get(
            reverse('vibe_coding:canvas_state_by_id', args=[self.canvas.id])
        )
        self.assertEqual(response.status_code, 200)
        
        data = json.loads(response.content)
        self.assertTrue(data['success'])
        self.assertEqual(data['canvas']['width'], 100)
        self.assertEqual(data['canvas']['height'], 100)
        self.assertIn('10,10', data['pixels'])
    
    def test_pixel_history_api(self):
        """Test the pixel history API endpoint"""
        PixelHistory.objects.create(
            canvas=self.canvas,
            x=5,
            y=5,
            color="#0000FF",
            placed_by=self.user
        )
        
        response = self.client.get(
            reverse('vibe_coding:pixel_history'),
            {'canvas_id': self.canvas.id, 'limit': 10}
        )
        self.assertEqual(response.status_code, 200)
        
        data = json.loads(response.content)
        self.assertTrue(data['success'])
        self.assertEqual(len(data['history']), 1)
        self.assertEqual(data['history'][0]['color'], '#0000FF')


class PixelPlacementTest(TestCase):
    """Test pixel placement functionality"""
    
    def setUp(self):
        self.client = Client()
        self.canvas = PixelCanvas.objects.create(
            name="Lux Pixel War",
            width=100,
            height=100,
            anonymous_cooldown_seconds=30,
            registered_cooldown_seconds=12,
            anonymous_pixels_per_minute=2,
            registered_pixels_per_minute=5
        )
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
    
    def test_place_pixel_anonymous(self):
        """Test anonymous user placing a pixel"""
        response = self.client.post(
            reverse('vibe_coding:place_pixel'),
            json.dumps({
                'x': 50,
                'y': 50,
                'color': '#FF0000',
                'canvas_id': self.canvas.id
            }),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['success'])
        self.assertEqual(data['pixel']['x'], 50)
        self.assertEqual(data['pixel']['y'], 50)
        self.assertEqual(data['pixel']['color'], '#FF0000')
        self.assertEqual(data['pixel']['placed_by'], 'Anonymous')
        
        # Verify pixel was created
        pixel = Pixel.objects.get(canvas=self.canvas, x=50, y=50)
        self.assertEqual(pixel.color, '#FF0000')
    
    def test_place_pixel_authenticated(self):
        """Test authenticated user placing a pixel"""
        self.client.login(username='testuser', password='testpass123')
        
        response = self.client.post(
            reverse('vibe_coding:place_pixel'),
            json.dumps({
                'x': 25,
                'y': 25,
                'color': '#00FF00',
                'canvas_id': self.canvas.id
            }),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['success'])
        self.assertEqual(data['pixel']['placed_by'], 'testuser')
        self.assertEqual(data['cooldown_info']['cooldown_seconds'], 12)
        self.assertEqual(data['cooldown_info']['pixels_remaining'], 4)
    
    def test_pixel_validation(self):
        """Test pixel placement validation"""
        # Test out of bounds
        response = self.client.post(
            reverse('vibe_coding:place_pixel'),
            json.dumps({
                'x': 150,  # Out of bounds
                'y': 50,
                'color': '#FF0000',
                'canvas_id': self.canvas.id
            }),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertEqual(data['error'], 'Invalid coordinates')
    
    def test_canvas_inactive(self):
        """Test placing pixel on inactive canvas"""
        self.canvas.is_active = False
        self.canvas.save()
        
        response = self.client.post(
            reverse('vibe_coding:place_pixel'),
            json.dumps({
                'x': 50,
                'y': 50,
                'color': '#FF0000',
                'canvas_id': self.canvas.id
            }),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertEqual(data['error'], 'Canvas is not active')


class CooldownTest(TestCase):
    """Test cooldown and rate limiting functionality"""
    
    def setUp(self):
        self.client = Client()
        self.canvas = PixelCanvas.objects.create(
            name="Lux Pixel War",
            width=100,
            height=100,
            anonymous_cooldown_seconds=30,
            registered_cooldown_seconds=12,
            anonymous_pixels_per_minute=2,
            registered_pixels_per_minute=5
        )
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
    
    def test_anonymous_cooldown(self):
        """Test anonymous user cooldown"""
        # Place first pixel
        response1 = self.client.post(
            reverse('vibe_coding:place_pixel'),
            json.dumps({
                'x': 10,
                'y': 10,
                'color': '#FF0000',
                'canvas_id': self.canvas.id
            }),
            content_type='application/json'
        )
        self.assertEqual(response1.status_code, 200)
        
        # Try to place second pixel immediately
        response2 = self.client.post(
            reverse('vibe_coding:place_pixel'),
            json.dumps({
                'x': 20,
                'y': 20,
                'color': '#00FF00',
                'canvas_id': self.canvas.id
            }),
            content_type='application/json'
        )
        
        self.assertEqual(response2.status_code, 429)
        data = json.loads(response2.content)
        self.assertEqual(data['error'], 'Cooldown active')
        self.assertIn('cooldown_remaining', data)
    
    def test_registered_user_cooldown(self):
        """Test registered user cooldown is shorter"""
        self.client.login(username='testuser', password='testpass123')
        
        # Place first pixel
        response1 = self.client.post(
            reverse('vibe_coding:place_pixel'),
            json.dumps({
                'x': 15,
                'y': 15,
                'color': '#0000FF',
                'canvas_id': self.canvas.id
            }),
            content_type='application/json'
        )
        
        data1 = json.loads(response1.content)
        self.assertEqual(data1['cooldown_info']['cooldown_seconds'], 12)
        self.assertEqual(data1['cooldown_info']['pixels_remaining'], 4)
    
    def test_pixels_per_minute_limit(self):
        """Test the per-minute pixel limit"""
        self.client.login(username='testuser', password='testpass123')
        
        # Mock time to bypass cooldown
        with patch('vibe_coding.views.timezone.now') as mock_now:
            base_time = timezone.now()
            
            # Place 5 pixels (the limit for registered users)
            for i in range(5):
                mock_now.return_value = base_time + timedelta(seconds=i*13)
                
                # Create a new cooldown record for the first pixel
                if i == 0:
                    UserPixelCooldown.objects.create(
                        user=self.user,
                        canvas=self.canvas,
                        pixels_placed_last_minute=0,
                        last_minute_reset=base_time
                    )
                
                response = self.client.post(
                    reverse('vibe_coding:place_pixel'),
                    json.dumps({
                        'x': i * 10,
                        'y': i * 10,
                        'color': '#FF0000',
                        'canvas_id': self.canvas.id
                    }),
                    content_type='application/json'
                )
                
                if i < 5:
                    self.assertEqual(response.status_code, 200)
            
            # Try to place 6th pixel within the same minute
            mock_now.return_value = base_time + timedelta(seconds=65)
            response = self.client.post(
                reverse('vibe_coding:place_pixel'),
                json.dumps({
                    'x': 60,
                    'y': 60,
                    'color': '#FF0000',
                    'canvas_id': self.canvas.id
                }),
                content_type='application/json'
            )
            
            # Should succeed as it's past the minute
            self.assertEqual(response.status_code, 200)


class PixelWarIntegrationTest(TestCase):
    """Integration tests for the complete pixel war flow"""
    
    def setUp(self):
        self.client = Client()
        self.user1 = User.objects.create_user(
            username='player1',
            password='pass123'
        )
        self.user2 = User.objects.create_user(
            username='player2',
            password='pass123'
        )
    
    def test_complete_game_flow(self):
        """Test a complete game flow with multiple users"""
        # Load the game page
        response = self.client.get(reverse('vibe_coding:pixel_war'))
        self.assertEqual(response.status_code, 200)
        
        # Get canvas from context
        canvas = PixelCanvas.objects.first()
        self.assertIsNotNone(canvas)
        
        # Player 1 places a pixel
        self.client.login(username='player1', password='pass123')
        response1 = self.client.post(
            reverse('vibe_coding:place_pixel'),
            json.dumps({
                'x': 10,
                'y': 10,
                'color': '#FF0000',
                'canvas_id': canvas.id
            }),
            content_type='application/json'
        )
        self.assertEqual(response1.status_code, 200)
        
        # Player 2 places a pixel at different location
        self.client.login(username='player2', password='pass123')
        response2 = self.client.post(
            reverse('vibe_coding:place_pixel'),
            json.dumps({
                'x': 20,
                'y': 20,
                'color': '#00FF00',
                'canvas_id': canvas.id
            }),
            content_type='application/json'
        )
        self.assertEqual(response2.status_code, 200)
        
        # Check canvas state
        response_state = self.client.get(
            reverse('vibe_coding:canvas_state_by_id', args=[canvas.id])
        )
        data = json.loads(response_state.content)
        self.assertEqual(len(data['pixels']), 2)
        self.assertIn('10,10', data['pixels'])
        self.assertIn('20,20', data['pixels'])
        
        # Check history
        response_history = self.client.get(
            reverse('vibe_coding:pixel_history'),
            {'canvas_id': canvas.id}
        )
        history_data = json.loads(response_history.content)
        self.assertEqual(len(history_data['history']), 2)
        
        # Check user stats
        stats1 = UserPixelStats.objects.get(user=self.user1, canvas=canvas)
        stats2 = UserPixelStats.objects.get(user=self.user2, canvas=canvas)
        self.assertEqual(stats1.total_pixels_placed, 1)
        self.assertEqual(stats2.total_pixels_placed, 1)
    
    def test_pixel_overwrite(self):
        """Test that pixels can be overwritten"""
        canvas = PixelCanvas.objects.create(
            name="Test Canvas",
            width=50,
            height=50
        )
        
        # Place first pixel
        response1 = self.client.post(
            reverse('vibe_coding:place_pixel'),
            json.dumps({
                'x': 5,
                'y': 5,
                'color': '#FF0000',
                'canvas_id': canvas.id
            }),
            content_type='application/json'
        )
        self.assertEqual(response1.status_code, 200)
        
        # Wait for cooldown
        import time
        time.sleep(1)
        
        # Overwrite the pixel with different color
        self.client.login(username='player1', password='pass123')
        response2 = self.client.post(
            reverse('vibe_coding:place_pixel'),
            json.dumps({
                'x': 5,
                'y': 5,
                'color': '#0000FF',
                'canvas_id': canvas.id
            }),
            content_type='application/json'
        )
        
        # Check that pixel was updated
        pixel = Pixel.objects.get(canvas=canvas, x=5, y=5)
        self.assertEqual(pixel.color, '#0000FF')
        
        # Check history has both placements
        history = PixelHistory.objects.filter(canvas=canvas, x=5, y=5)
        self.assertEqual(history.count(), 2)


class PixelWarJavaScriptTest(TestCase):
    """Test JavaScript functionality through API interactions"""
    
    def setUp(self):
        self.client = Client()
        self.canvas = PixelCanvas.objects.create(
            name="JS Test Canvas",
            width=50,
            height=50
        )
    
    def test_api_response_format(self):
        """Test that API responses match expected JavaScript format"""
        response = self.client.get(
            reverse('vibe_coding:canvas_state_by_id', args=[self.canvas.id])
        )
        data = json.loads(response.content)
        
        # Check required fields for JavaScript
        self.assertIn('success', data)
        self.assertIn('canvas', data)
        self.assertIn('pixels', data)
        
        canvas_data = data['canvas']
        self.assertIn('id', canvas_data)
        self.assertIn('width', canvas_data)
        self.assertIn('height', canvas_data)
        self.assertIn('anonymous_cooldown', canvas_data)
        self.assertIn('registered_cooldown', canvas_data)
        self.assertIn('is_active', canvas_data)
    
    def test_place_pixel_response_format(self):
        """Test pixel placement response format for JavaScript"""
        response = self.client.post(
            reverse('vibe_coding:place_pixel'),
            json.dumps({
                'x': 10,
                'y': 10,
                'color': '#123456',
                'canvas_id': self.canvas.id
            }),
            content_type='application/json'
        )
        
        data = json.loads(response.content)
        
        # Check success response format
        self.assertIn('success', data)
        self.assertIn('pixel', data)
        self.assertIn('cooldown_info', data)
        
        pixel_data = data['pixel']
        self.assertIn('x', pixel_data)
        self.assertIn('y', pixel_data)
        self.assertIn('color', pixel_data)
        self.assertIn('placed_by', pixel_data)
        
        cooldown_data = data['cooldown_info']
        self.assertIn('cooldown_seconds', cooldown_data)
        self.assertIn('pixels_remaining', cooldown_data)
        self.assertIn('is_registered', cooldown_data)
