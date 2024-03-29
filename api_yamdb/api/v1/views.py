import uuid

from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db.models import Avg
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.generics import CreateAPIView, GenericAPIView
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from reviews.models import Category, Genre, Review, Title
from .filters import FilterTitleSet
from .viewsets import CreateListDestroy
from .permissions import (AdminOnlyPermission, AuthOwnerPermission,
                          ReviewsAndCommentsPermission, TitlesPermission)
from .serializers import (CategorySerializer, CommentSerializer,
                          GenreSerializer, GetTitleSerializer,
                          PostTitleSerializer, ReviewSerializer,
                          TokenSerializer, UserRegistrationSerializer,
                          UserSerializer)

User = get_user_model()


class RegisterView(CreateAPIView):
    """
    Регистрация нового пользователя.
    v1/auth/signup/
    """
    permission_classes = [permissions.AllowAny]
    serializer_class = UserRegistrationSerializer

    def generate_confirmation_code(self):
        """
        Генерирует случайный код подтверждения.
        """
        code = str(uuid.uuid4())
        return code

    def create(self, request, *args, **kwargs):
        """
        Создание пользователя.
        1. Генерирует код подтверждения.
        2. Отправляет письмо с кодом.
        3. Возвращает username и email.
        4. Запрещает повторную регистрацию.
        HTTP status создания пользователя изменен на 200.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data['email']
        username = serializer.validated_data['username']

        existing_user = User.objects.filter(email=email,
                                            username=username).first()

        # Если пользователь уже существует:
        # Создает новый код подтверждения и обновляет его в БД.
        if existing_user:
            confirmation_code = self.generate_confirmation_code()
            existing_user.confirmation_code = confirmation_code
            existing_user.save()

        # Если пользователь не существует:
        else:
            # Создает новый код подтверждения.
            confirmation_code = self.generate_confirmation_code()
            # Добавляет confirmation_code в контекст,
            # Сериализатор сохранит его в БД, вместе с новым пользователем.
            serializer.context['confirmation_code'] = confirmation_code
            self.perform_create(serializer)

        response_data = {'username': username, 'email': email}

        send_mail(
            from_email=None,
            message=f'Код подтверждения: {confirmation_code}',
            subject='Confirmation Code',
            recipient_list=(email,)
        )
        return Response(response_data, status=status.HTTP_200_OK)


class TokenView(GenericAPIView):
    """
    Получение Токена.
    v1/auth/token/
    """
    permission_classes = [permissions.AllowAny]
    serializer_class = TokenSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user

        refresh = RefreshToken.for_user(user)
        token = {'access': str(refresh.access_token)}
        return Response(token)


class UsersViewSet(viewsets.ModelViewSet):
    """Эндпойнт v1/users."""
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [AdminOnlyPermission]
    filter_backends = [filters.SearchFilter]
    search_fields = ['username']
    http_method_names = [  # Метод 'put' исключен.
        'get', 'post', 'patch', 'delete', 'head', 'options']
    lookup_field = 'username'  # ./users/rea/ вместо ./users/1/

    @action(detail=False, methods=['GET', 'PATCH'],
            permission_classes=[AuthOwnerPermission])
    def me(self, request):
        """
        Получение и Изменение своего профиля.
        Эндпойнт .v1/users/me/
        """
        user = request.user
        msg = 'У вас нет разрешения изменять роль пользователя.'

        if request.method == 'PATCH':
            serializer = UserSerializer(user, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            # role может менять только админ.
            if ('role' in serializer.validated_data
                    and user.role != user.ADMIN or user.is_superuser):
                return Response({'role error:': msg},
                                status=status.HTTP_403_FORBIDDEN)
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        # Метод GET.
        serializer = UserSerializer(user)
        return Response(serializer.data)


class GenreViewSet(CreateListDestroy):
    """View-функция для жанров произведений."""

    queryset = Genre.objects.all()
    serializer_class = GenreSerializer


class CategoryViewSet(CreateListDestroy):
    """View-функция для категорий произведений."""

    queryset = Category.objects.all()
    serializer_class = CategorySerializer


class TitleViewSet(viewsets.ModelViewSet):
    """View-функция для произведений."""

    queryset = (Title.objects.all().select_related('category')
                .prefetch_related('genre')
                .annotate(rating_avg=Avg('reviews__score'))
                .order_by('id'))
    permission_classes = [TitlesPermission]
    filter_backends = (DjangoFilterBackend,)
    filterset_class = FilterTitleSet

    def get_serializer_class(self):
        if self.action in ('list', 'retrieve'):
            return GetTitleSerializer
        return PostTitleSerializer


class ReviewViewSet(viewsets.ModelViewSet):
    """View-функция для отзывов."""

    serializer_class = ReviewSerializer

    permission_classes = [ReviewsAndCommentsPermission]

    def get_queryset(self):
        title = get_object_or_404(Title, id=self.kwargs.get('title_id'))
        return title.reviews.all()

    def perform_create(self, serializer):
        title = get_object_or_404(Title, id=self.kwargs.get('title_id'))
        serializer.save(author=self.request.user, title=title)


class CommentViewSet(viewsets.ModelViewSet):
    """View-функция для комментариев."""

    serializer_class = CommentSerializer

    permission_classes = [ReviewsAndCommentsPermission]

    def get_queryset(self):
        review = get_object_or_404(Review, id=self.kwargs.get('review_id'))
        return review.comments.all()

    def perform_create(self, serializer):
        review = get_object_or_404(Review, id=self.kwargs.get('review_id'))
        serializer.save(author=self.request.user, review=review)
