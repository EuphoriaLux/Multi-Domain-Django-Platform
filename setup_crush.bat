@echo off
echo ========================================
echo Crush.lu Setup Script
echo ========================================
echo.

echo Step 1: Running migrations...
python manage.py makemigrations crush_lu
python manage.py migrate
echo.

echo Step 2: Creating Crush Coaches...
python manage.py create_crush_coaches
echo.

echo Step 3: Creating Sample Events...
python manage.py create_sample_events
echo.

echo ========================================
echo Setup Complete!
echo ========================================
echo.
echo Crush.lu is ready to use!
echo.
echo Access the platform at:
echo   http://localhost:8000/crush/
echo.
echo Coach login credentials:
echo   Username: coach.marie
echo   Password: crushcoach2025
echo.
echo Start the server with:
echo   python manage.py runserver
echo.
pause
