"""
Generate VAPID keys for Web Push notifications
"""

try:
    from py_vapid import Vapid

    print("Generating VAPID keys...")
    print("=" * 70)

    vapid = Vapid()
    vapid.generate_keys()

    # Try the new API first
    try:
        public_key = vapid.public_key.public_bytes_raw()
        private_key = vapid.private_key.private_bytes_raw()
    except AttributeError:
        # Fallback to old API
        try:
            public_key = vapid.public_key.savePublicKey()
            private_key = vapid.private_key.savePrivateKey()
        except AttributeError:
            # Manual extraction
            from cryptography.hazmat.primitives import serialization

            public_key = vapid.public_key.public_bytes(
                encoding=serialization.Encoding.X962,
                format=serialization.PublicFormat.UncompressedPoint
            )

            private_key = vapid.private_key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )

    # Convert to base64 for use in settings
    import base64

    # URL-safe base64 encoding (required for VAPID)
    public_key_b64 = base64.urlsafe_b64encode(public_key).decode('utf-8').rstrip('=')
    private_key_b64 = base64.urlsafe_b64encode(private_key).decode('utf-8').rstrip('=')

    print("\n✅ VAPID Keys Generated Successfully!")
    print("=" * 70)
    print("\nPublic Key:")
    print(public_key_b64)
    print("\nPrivate Key:")
    print(private_key_b64)
    print("\n" + "=" * 70)
    print("\n📋 Add these to your environment variables (.env file or Azure settings):")
    print("=" * 70)
    print(f"\nVAPID_PUBLIC_KEY={public_key_b64}")
    print(f"VAPID_PRIVATE_KEY={private_key_b64}")
    print("VAPID_ADMIN_EMAIL=noreply@crush.lu")
    print("\n" + "=" * 70)
    print("\n⚠️  IMPORTANT:")
    print("- Keep the private key SECRET (never commit to git)")
    print("- Add these to .env for local development")
    print("- Add to Azure App Service environment variables for production")
    print("- The public key is safe to use in frontend code")
    print("\n")

except ImportError:
    print("❌ Error: py-vapid not installed")
    print("\nRun: pip install py-vapid==1.9.1")
except Exception as e:
    print(f"❌ Error generating VAPID keys: {e}")
    print("\nTrying alternative method...")

    # Alternative: Use cryptography directly
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    import base64

    print("\nGenerating keys using cryptography library directly...")

    # Generate EC key pair
    private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
    public_key = private_key.public_key()

    # Serialize keys
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )

    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint
    )

    # URL-safe base64 encoding
    public_key_b64 = base64.urlsafe_b64encode(public_bytes).decode('utf-8').rstrip('=')
    private_key_b64 = base64.urlsafe_b64encode(private_bytes).decode('utf-8').rstrip('=')

    print("\n✅ VAPID Keys Generated Successfully (Alternative Method)!")
    print("=" * 70)
    print("\nPublic Key:")
    print(public_key_b64)
    print("\nPrivate Key:")
    print(private_key_b64)
    print("\n" + "=" * 70)
    print("\n📋 Add these to your environment variables:")
    print("=" * 70)
    print(f"\nVAPID_PUBLIC_KEY={public_key_b64}")
    print(f"VAPID_PRIVATE_KEY={private_key_b64}")
    print("VAPID_ADMIN_EMAIL=noreply@crush.lu")
    print("\n" + "=" * 70)
