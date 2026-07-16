FROM php:8.2-cli

RUN pecl install redis && docker-php-ext-enable redis

WORKDIR /app

COPY . .

EXPOSE 8000

CMD ["php", "-S", "0.0.0.0:8000", "-c", "php.ini"]
